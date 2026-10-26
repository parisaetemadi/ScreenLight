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

/// The half-light disc, filled from the bottom as the screen warms.
///
/// A template image: one colour plus transparency, which the system inverts
/// for the dark menu bar. Drawn rather than shipped as a file so there are no
/// assets to keep in step with it.
func discIcon(fill: Double) -> NSImage {
    let side: CGFloat = 18
    let image = NSImage(size: NSSize(width: side, height: side), flipped: false) { rect in
        let disc = NSBezierPath(ovalIn: rect.insetBy(dx: 1.75, dy: 1.75))
        if fill > 0.001 {
            NSGraphicsContext.saveGraphicsState()
            disc.addClip()
            let inner = rect.insetBy(dx: 1.75, dy: 1.75)
            NSColor.black.setFill()
            NSBezierPath(rect: NSRect(x: rect.minX,
                                      y: inner.minY,
                                      width: rect.width,
                                      height: inner.height * CGFloat(fill))).fill()
            NSGraphicsContext.restoreGraphicsState()
        }
        disc.lineWidth = 1.6
        NSColor.black.setStroke()
        disc.stroke()
        return true
    }
    image.isTemplate = true
    return image
}

/// Five steps for the whole evening. A mark that redraws on every daemon tick
/// would twitch in the corner of your eye, and the menu bar is meant to be
/// ignorable.
func coarse(_ progress: Double) -> Double {
    (min(max(progress, 0), 1) * 4).rounded() / 4
}

// MARK: - The app

final class ScreenLightApp: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var item: NSStatusItem!
    private let menu = NSMenu()
    private let header = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let follow = NSMenuItem(title: "Follow the sun", action: #selector(tapFollow), keyEquivalent: "")
    private let warm = NSMenuItem(title: "Warm now", action: #selector(tapWarm), keyEquivalent: "")
    private let off = NSMenuItem(title: "Off for an hour", action: #selector(tapOff), keyEquivalent: "")
    private var timer: Timer?
    private var status: Status?

    func applicationDidFinishLaunching(_ note: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = discIcon(fill: 0)

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
            item.button?.image = discIcon(fill: 0)
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
            item.button?.image = discIcon(fill: 0)
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

        // A manual hold shows whatever is held; otherwise the mark tracks the
        // schedule, which is the thing worth knowing at a glance.
        let fill = status.source == "manual"
            ? warmth(of: status.kelvin, day: status.day, night: status.night)
            : status.progress
        item.button?.image = discIcon(fill: coarse(fill))
    }

    /// Where a held temperature sits between the day and night targets, so a
    /// hand-set 2000K still reads as "warm" on the mark.
    private func warmth(of kelvin: Double, day: Double, night: Double) -> Double {
        guard day != night else { return 0 }
        return min(max((day - kelvin) / (day - night), 0), 1)
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
