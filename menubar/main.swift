// ScreenLight — a menu bar face for the screenlight daemon.
//
// This deliberately holds no logic of its own. Only one process may own a
// display's gamma table, and the daemon owns it; this app reads
// `screenlight status --json` and asks for changes through the same CLI a
// person would type. That keeps one implementation of the schedule rather
// than a second one here that could drift out of step with it.

import Cocoa

// MARK: - What the CLI tells us

struct Status: Decodable {
    let kelvin: Double
    let brightness: Double
    let source: String      // auto | dimmed | manual | unlocated
    let preset: String?
    let located: Bool
    let daemon: Int?
    let day: Double
    let night: Double
    let progress: Double    // 0 at full day, 1 at full night
    let moon: Double        // 0 new, 0.25 first quarter, 0.5 full
}

enum CLI {
    /// Where `screenlight install` puts the symlink, plus the usual suspects.
    static let path: String? = {
        let candidates: [String] = [
            ProcessInfo.processInfo.environment["SCREENLIGHT_CLI"],
            ("~/.local/bin/screenlight" as NSString).expandingTildeInPath,
            "/usr/local/bin/screenlight",
            "/opt/homebrew/bin/screenlight",
        ].compactMap { $0 }
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }()

    @discardableResult
    static func run(_ arguments: [String]) -> String? {
        guard let path = path else { return nil }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: path)
        task.arguments = arguments
        let out = Pipe()
        task.standardOutput = out
        task.standardError = Pipe()
        do {
            try task.run()
        } catch {
            return nil
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        return String(data: data, encoding: .utf8)
    }

    static func status() -> Status? {
        guard let text = run(["status", "--json"]),
              let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(Status.self, from: data)
    }
}

// MARK: - The mark

/// Tonight's moon, at the phase it actually is.
///
/// A template image: one colour plus transparency, which the system inverts
/// for the dark menu bar. Drawn rather than shipped as a file, which is the
/// only way a mark that changes every night could work at all.
func moonIcon(phase: Double) -> NSImage {
    let side: CGFloat = 18
    let image = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
        let ring = rect.insetBy(dx: 1.75, dy: 1.75)
        // The moon sits inside the ring with a gap, so a full moon reads as a
        // disc within a circle rather than as one solid blob.
        let well = ring.insetBy(dx: 1.9, dy: 1.9)
        let r = well.width / 2
        let cx = well.midX, cy = well.midY

        let waxing = phase < 0.5
        // Half-width of the terminator, signed. Positive curves into the lit
        // limb, giving a crescent; negative bulges past centre, giving a
        // gibbous. At new it equals the radius and the lit area vanishes; at
        // full it is the radius the other way and the disc fills.
        let t = CGFloat(cos(2 * Double.pi * phase)) * (waxing ? 1 : -1) * r

        let lit = NSBezierPath()
        // The limb: whichever semicircle faces the sun, top to bottom.
        lit.appendArc(withCenter: NSPoint(x: cx, y: cy), radius: r,
                      startAngle: 90, endAngle: waxing ? -90 : 270,
                      clockwise: waxing)
        // The terminator, back up to the top: half an ellipse as two quarter
        // Beziers. 0.5523 is the usual circle-to-cubic constant.
        let k: CGFloat = 0.5522847498
        lit.curve(to: NSPoint(x: cx + t, y: cy),
                  controlPoint1: NSPoint(x: cx + k * t, y: cy - r),
                  controlPoint2: NSPoint(x: cx + t, y: cy - k * r))
        lit.curve(to: NSPoint(x: cx, y: cy + r),
                  controlPoint1: NSPoint(x: cx + t, y: cy + k * r),
                  controlPoint2: NSPoint(x: cx + k * t, y: cy + r))
        lit.close()

        NSColor.black.setFill()
        lit.fill()

        let disc = NSBezierPath(ovalIn: ring)
        disc.lineWidth = 1.6
        NSColor.black.setStroke()
        disc.stroke()
        return true
    }
    image.isTemplate = true
    return image
}

// MARK: - The app

final class ScreenLightApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let header = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let follow = NSMenuItem(title: "Automatic", action: #selector(tapFollow), keyEquivalent: "")
    private let warm = NSMenuItem(title: "Warm now", action: #selector(tapWarm), keyEquivalent: "")
    private let off = NSMenuItem(title: "Off for an hour", action: #selector(tapOff), keyEquivalent: "")
    private var timer: Timer?
    private var status: Status?

    func applicationDidFinishLaunching(_ note: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = moonIcon(phase: 0.5)

        header.isEnabled = false
        for entry in [follow, warm, off] { entry.target = self }

        // AppKit enables and disables menu items for you by asking whether the
        // target responds to the action, which would override the disabling
        // done below when the CLI is missing. Take the wheel.
        menu.autoenablesItems = false
        menu.delegate = self
        menu.addItem(header)
        menu.addItem(.separator())
        menu.addItem(follow)
        menu.addItem(warm)
        menu.addItem(off)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit ScreenLight",
                                action: #selector(NSApplication.terminate(_:)),
                                keyEquivalent: "q"))
        item.menu = menu

        refresh()
        // The schedule moves slowly; this is only so the mark is not stale when
        // nobody has opened the menu. Opening it refreshes too.
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        refresh()
    }

    /// Asks the CLI for the current state off the main thread.
    ///
    /// Reading it means starting a Python process, which is tens of
    /// milliseconds at best. On the main thread that is a visible stall every
    /// time the menu opens, so the work happens elsewhere and the menu redraws
    /// when the answer arrives.
    private func refresh() {
        guard CLI.path != nil else {
            header.title = "screenlight CLI not found — run screenlight install"
            for entry in [follow, warm, off] { entry.isEnabled = false }
            item.button?.image = moonIcon(phase: 0.5)
            return
        }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let fresh = CLI.status()
            DispatchQueue.main.async { self?.apply(fresh) }
        }
    }

    private func apply(_ fresh: Status?) {
        status = fresh
        guard let status = fresh else {
            header.title = "Cannot read screenlight"
            for entry in [follow, warm, off] { entry.isEnabled = false }
            item.button?.image = moonIcon(phase: 0.5)
            return
        }

        for entry in [follow, warm, off] { entry.isEnabled = true }
        let kelvin = Int(status.kelvin.rounded())

        if status.daemon == nil {
            header.title = "Daemon not running — run screenlight install"
        } else if !status.located {
            header.title = "No location set — run screenlight config --locate"
        } else {
            switch status.source {
            case "manual":
                header.title = "\(kelvin)K · held by hand"
            case "dimmed":
                header.title = "\(kelvin)K · \(Int((status.brightness * 100).rounded()))% brightness"
            default:
                header.title = "\(kelvin)K · \(Int((status.progress * 100).rounded()))% toward night"
            }
        }

        follow.state = (status.source == "auto" || status.source == "dimmed") ? .on : .off
        warm.state = (status.source == "manual" && kelvin <= Int(status.night.rounded())) ? .on : .off
        warm.title = "Warm now (\(Int(status.night.rounded()))K)"

        // The mark is the moon, not a gauge. What the screen is doing is in
        // the header a click away; what the sky is doing cannot be read off
        // anything else, and it is the better thing to carry in a bar you are
        // meant to ignore.
        item.button?.image = moonIcon(phase: status.moon)
    }

    private func ask(_ arguments: [String]) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            CLI.run(arguments)
            let fresh = CLI.status()
            DispatchQueue.main.async { self?.apply(fresh) }
        }
    }

    @objc private func tapFollow() { ask(["auto"]) }

    @objc private func tapWarm() {
        ask(["set", String(Int((status?.night ?? 2700).rounded()))])
    }

    @objc private func tapOff() { ask(["off", "--for", "60"]) }
}

let application = NSApplication.shared
let delegate = ScreenLightApp()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
