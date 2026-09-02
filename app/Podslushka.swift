// Подслушка — панель управления записью звонков.
// Сборка: ./build-app.sh   (нужны Command Line Tools)
//
// Приложение не пишет звук само — оно дёргает call-rec.sh, который уже
// проверен. На себя берёт: панель, таймер, переключение выхода на Podslushka Out
// и обратно, создание аудиоустройств через CoreAudio и запуск картотеки.

import AppKit
import CoreAudio
import Foundation

// MARK: - CoreAudio, тонкая обёртка

enum CA {
    static func addr(_ sel: AudioObjectPropertySelector,
                     _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal)
    -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: sel, mScope: scope,
                                   mElement: kAudioObjectPropertyElementMain)
    }

    static func allDevices() -> [AudioObjectID] {
        var a = addr(kAudioHardwarePropertyDevices)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject),
                                             &a, 0, nil, &size) == noErr else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                         &a, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    static func string(_ id: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
        var a = addr(sel)
        var size = UInt32(MemoryLayout<CFString?>.size)
        var out: CFString? = nil
        let st = withUnsafeMutablePointer(to: &out) {
            AudioObjectGetPropertyData(id, &a, 0, nil, &size, $0)
        }
        guard st == noErr, let s = out else { return nil }
        return s as String
    }

    static func name(_ id: AudioObjectID) -> String { string(id, kAudioObjectPropertyName) ?? "?" }
    static func uid(_ id: AudioObjectID) -> String? { string(id, kAudioDevicePropertyDeviceUID) }

    static func channels(_ id: AudioObjectID, input: Bool) -> Int {
        var a = addr(kAudioDevicePropertyStreamConfiguration,
                     input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, raw) == noErr else { return 0 }
        let list = raw.assumingMemoryBound(to: AudioBufferList.self)
        let bufs = UnsafeMutableAudioBufferListPointer(list)
        return bufs.reduce(0) { $0 + Int($1.mNumberChannels) }
    }

    static func inputs() -> [AudioObjectID] { allDevices().filter { channels($0, input: true) > 0 } }
    static func outputs() -> [AudioObjectID] { allDevices().filter { channels($0, input: false) > 0 } }

    static func find(name target: String) -> AudioObjectID? {
        allDevices().first { name($0) == target }
    }

    static var defaultOutput: AudioObjectID {
        get {
            var a = addr(kAudioHardwarePropertyDefaultOutputDevice)
            var id = AudioObjectID(0)
            var size = UInt32(MemoryLayout<AudioObjectID>.size)
            AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                       &a, 0, nil, &size, &id)
            return id
        }
        set {
            var a = addr(kAudioHardwarePropertyDefaultOutputDevice)
            var id = newValue
            AudioObjectSetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil,
                                       UInt32(MemoryLayout<AudioObjectID>.size), &id)
        }
    }

    /// Собрать агрегатное (stacked = устройство с многими выходами) устройство.
    /// Строковые ключи — из AudioHardware.h, чтобы не зависеть от импорта макросов.
    static func createAggregate(name: String, uid deviceUID: String,
                                subUIDs: [String], master: String,
                                driftOn: Set<String>, stacked: Bool) -> String? {
        if let old = find(name: name) {
            AudioHardwareDestroyAggregateDevice(old)
        }
        let subs: [[String: Any]] = subUIDs.map { u in
            var d: [String: Any] = ["uid": u]
            if driftOn.contains(u) { d["drift"] = 1 }
            return d
        }
        let desc: [String: Any] = [
            "name": name,
            "uid": deviceUID,
            "subdevices": subs,
            "master": master,
            "stacked": stacked ? 1 : 0,
            "private": 0,
        ]
        var newID = AudioObjectID(0)
        let st = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &newID)
        if st != noErr { return "CoreAudio вернул код \(st)" }
        return nil
    }
}

// Журнал пишется всегда в ~/.podslushka/podslushka.log — так разбор
// поломки не требует держать приложение запущенным из терминала.
let appHome = NSString(string:
    ProcessInfo.processInfo.environment["PODSLUSHKA_HOME"] ?? "~/.podslushka"
).expandingTildeInPath
let logURL = URL(fileURLWithPath: appHome).appendingPathComponent("podslushka.log")

func dbg(_ m: String) {
    try? FileManager.default.createDirectory(atPath: appHome,
                                             withIntermediateDirectories: true)
    let stamp = ISO8601DateFormatter().string(from: Date())
    let line = "\(stamp) \(m)\n"
    if ProcessInfo.processInfo.environment["PODSLUSHKA_DEBUG"] == "1" {
        FileHandle.standardError.write(("[подслушка] " + m + "\n").data(using: .utf8)!)
    }
    guard let data = line.data(using: .utf8) else { return }
    if let h = try? FileHandle(forWritingTo: logURL) {
        h.seekToEndOfFile(); h.write(data); try? h.close()
    } else {
        try? data.write(to: logURL)
    }
}

// MARK: - Приложение

final class App: NSObject, NSApplicationDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let menu = NSMenu()
    let scripts = URL(fileURLWithPath: appHome).appendingPathComponent("bin")
    let pidFile = URL(fileURLWithPath: "/tmp/podslushka/ffmpeg.pid")

    var recording = false
    var busy = false                 // идёт расшифровка
    var startedAt: Date?
    var savedOutput: AudioObjectID?
    var ticker: Timer?
    var menuOpen = false

    let defaults = UserDefaults.standard
    var micName: String {
        get { defaults.string(forKey: "mic") ?? "Микрофон MacBook Pro" }
        set { defaults.set(newValue, forKey: "mic") }
    }
    var outName: String {
        get { defaults.string(forKey: "out") ?? "Динамики MacBook Pro" }
        set { defaults.set(newValue, forKey: "out") }
    }
    var inDock: Bool {
        get { defaults.bool(forKey: "dock") }
        set {
            defaults.set(newValue, forKey: "dock")
            NSApp.setActivationPolicy(newValue ? .regular : .accessory)
        }
    }
    var autoSwitch: Bool {
        get { defaults.object(forKey: "autoSwitch") == nil ? true : defaults.bool(forKey: "autoSwitch") }
        set { defaults.set(newValue, forKey: "autoSwitch") }
    }

    /// Команды без интерфейса — на случай, когда до значка в панели не добраться.
    /// Возвращает true, если работа сделана и жить дальше незачем.
    func runCLI() -> Bool {
        let args = CommandLine.arguments
        func value(after key: String) -> String? {
            guard let i = args.firstIndex(of: key), i + 1 < args.count else { return nil }
            return args[i + 1]
        }

        var didWork = false
        if let m = value(after: "--mic") {
            micName = m
            print("микрофон: \(m)")
            didWork = true
        }
        if let o = value(after: "--out") {
            outName = o
            print("выход: \(o)")
            didWork = true
        }
        if args.contains("--devices") {
            print("входы:")
            for d in CA.inputs() { print("  " + CA.name(d)) }
            print("выходы:")
            for d in CA.outputs() { print("  " + CA.name(d)) }
            didWork = true
        }
        if args.contains("--restore-output") {
            if let name = restoreOutputNow() {
                print("выход возвращён на \(name)")
            } else {
                print("не нашёл, куда возвращать — выбери выход вручную")
            }
            didWork = true
        }
        if args.contains("--make-devices") {
            makeDevices(silent: true)
            didWork = true
        }
        return didWork
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        if runCLI() { NSApp.terminate(nil); return }
        // Если строка меню переполнена (частое дело на макбуках с чёлкой), значок
        // может быть не виден вовсе. Тогда до меню добираются через Dock:
        // open -a Podslushka --args --dock
        if CommandLine.arguments.contains("--dock") { defaults.set(true, forKey: "dock") }
        if CommandLine.arguments.contains("--no-dock") { defaults.set(false, forKey: "dock") }
        NSApp.setActivationPolicy(inDock ? .regular : .accessory)
        installMainMenu()

        redrawIcon()
        rebuildMenu()
        if panelVisible { buildPanel() }
        fixStuckOutput()
        menu.delegate = self
        item.menu = menu
        dbg("=== старт, pid \(ProcessInfo.processInfo.processIdentifier), кнопка: \(item.button != nil ? "есть" : "НЕТ"), пунктов \(menu.numberOfItems), политика \(inDock ? "dock" : "меню")")
        ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    /// Приложению с иконкой в Dock положено иметь главное меню. Без него AppKit
    /// не даёт приложению нормально активироваться, и клик по элементу в строке
    /// меню уходит в никуда.
    func installMainMenu() {
        let bar = NSMenu()
        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "О приложении", action: nil, keyEquivalent: "")
        appMenu.addItem(.separator())
        let q = NSMenuItem(title: "Выйти", action: #selector(quit), keyEquivalent: "q")
        q.target = self
        appMenu.addItem(q)
        appItem.submenu = appMenu
        NSApp.mainMenu = bar
    }

    // MARK: плавающая панель

    var panel: NSPanel?
    var panelDot: NSView?
    var panelLabel: NSTextField?
    var panelButton: NSButton?

    var panelVisible: Bool {
        get { defaults.object(forKey: "panel") == nil ? true : defaults.bool(forKey: "panel") }
        set {
            defaults.set(newValue, forKey: "panel")
            if newValue { buildPanel() } else { closePanel() }
        }
    }

    /// Панель поверх всех окон и пространств, в том числе поверх полноэкранного
    /// звонка. Нужна потому, что значок в строке меню на макбуке с чёлкой
    /// может быть просто вытеснен и не показан вовсе.
    func buildPanel() {
        if panel != nil { return }
        let w: CGFloat = 210, h: CGFloat = 44
        let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: w, height: h),
                        styleMask: [.borderless, .nonactivatingPanel],
                        backing: .buffered, defer: false)
        p.isFloatingPanel = true
        p.level = .statusBar
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.isMovableByWindowBackground = true
        p.hidesOnDeactivate = false

        let bg = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: w, height: h))
        bg.material = .hudWindow
        bg.blendingMode = .behindWindow
        bg.state = .active
        bg.wantsLayer = true
        bg.layer?.cornerRadius = 12
        bg.layer?.masksToBounds = true
        p.contentView = bg

        let dot = NSView(frame: NSRect(x: 14, y: h / 2 - 5, width: 10, height: 10))
        dot.wantsLayer = true
        dot.layer?.cornerRadius = 5
        dot.layer?.backgroundColor = NSColor.systemGray.cgColor
        bg.addSubview(dot)

        let label = NSTextField(labelWithString: "Готов")
        label.frame = NSRect(x: 32, y: h / 2 - 9, width: 84, height: 18)
        label.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
        bg.addSubview(label)

        let rec = NSButton(title: "Запись", target: self, action: #selector(toggle))
        rec.frame = NSRect(x: 116, y: 9, width: 62, height: 26)
        rec.bezelStyle = .rounded
        rec.controlSize = .small
        bg.addSubview(rec)

        let more = NSButton(title: "•••", target: self, action: #selector(showPanelMenu(_:)))
        more.frame = NSRect(x: 178, y: 9, width: 26, height: 26)
        more.bezelStyle = .rounded
        more.controlSize = .small
        bg.addSubview(more)

        if let saved = defaults.string(forKey: "panelPos") {
            p.setFrameOrigin(NSPointFromString(saved))
        } else if let vis = NSScreen.main?.visibleFrame {
            p.setFrameOrigin(NSPoint(x: vis.maxX - w - 20, y: vis.maxY - h - 12))
        }
        p.delegate = self
        p.orderFrontRegardless()

        panel = p
        panelDot = dot
        panelLabel = label
        panelButton = rec
        updatePanel()
        dbg("панель показана")
    }

    func closePanel() {
        panel?.close()
        panel = nil
        panelDot = nil
        panelLabel = nil
        panelButton = nil
    }

    @objc func togglePanel() {
        panelVisible = !panelVisible
        rebuildMenu()
    }

    @objc func showPanelMenu(_ sender: NSButton) {
        rebuildMenu()
        menu.popUp(positioning: nil,
                   at: NSPoint(x: 0, y: sender.bounds.height + 4),
                   in: sender)
    }

    func updatePanel() {
        guard let dot = panelDot, let label = panelLabel, let rec = panelButton else { return }
        if recording, let t = startedAt {
            dot.layer?.backgroundColor = NSColor.systemRed.cgColor
            label.stringValue = "Идёт " + clock(Date().timeIntervalSince(t))
            rec.title = "Стоп"
            rec.isEnabled = true
        } else if busy {
            dot.layer?.backgroundColor = NSColor.systemOrange.cgColor
            label.stringValue = "Расшифровка"
            rec.title = "Ждём"
            rec.isEnabled = false
        } else {
            dot.layer?.backgroundColor = NSColor.systemGray.cgColor
            label.stringValue = "Готов"
            rec.title = "Запись"
            rec.isEnabled = true
        }
    }

    // MARK: состояние

    var externalPID: Int32? {
        guard let s = try? String(contentsOf: pidFile, encoding: .utf8),
              let pid = Int32(s.trimmingCharacters(in: .whitespacesAndNewlines)),
              kill(pid, 0) == 0 else { return nil }
        return pid
    }

    func tick() {
        let live = externalPID != nil
        if live != recording {
            recording = live
            if live && startedAt == nil { startedAt = Date() }
            if !live { startedAt = nil }
            rebuildMenu()
        }
        redrawIcon()
    }

    func redrawIcon() {
        guard let button = item.button else { return }
        let symbol = busy ? "waveform" : (recording ? "record.circle.fill" : "record.circle")
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: "Подслушка")
        img?.isTemplate = !recording
        button.image = img
        button.contentTintColor = recording ? .systemRed : nil

        // Если символа в системе не нашлось, кнопка без картинки и без текста
        // схлопывается в нулевую ширину и становится невидимой. Поэтому запасной
        // текст ставится всегда, когда картинки нет.
        let fallback = img == nil ? (recording ? "●" : (busy ? "…" : "◉")) : ""
        if recording, let t = startedAt {
            button.title = fallback + " " + clock(Date().timeIntervalSince(t))
        } else {
            button.title = busy ? (fallback + " …") : fallback
        }
        updatePanel()
    }

    func clock(_ s: TimeInterval) -> String {
        let v = Int(s)
        return v >= 3600
            ? String(format: "%d:%02d:%02d", v / 3600, v % 3600 / 60, v % 60)
            : String(format: "%d:%02d", v / 60, v % 60)
    }

    func applicationWillTerminate(_ n: Notification) {
        restoreOutput()
    }

    // MARK: меню

    func rebuildMenu() {
        if menuOpen { dbg("перестройка пропущена, меню открыто"); return }
        menu.removeAllItems()

        let mainSel: Selector? = busy ? nil : #selector(toggle)
        let mainTitle = busy ? "Расшифровка идёт…"
                             : (recording ? "Остановить запись" : "Записать звонок")
        let main = NSMenuItem(title: mainTitle, action: mainSel, keyEquivalent: "r")
        main.target = self
        menu.addItem(main)

        if recording, let t = startedAt {
            let info = NSMenuItem(title: "Идёт " + clock(Date().timeIntervalSince(t)), action: nil, keyEquivalent: "")
            info.isEnabled = false
            menu.addItem(info)
        }

        menu.addItem(.separator())

        let ui = NSMenuItem(title: "Картотека звонков…", action: #selector(openUI), keyEquivalent: "k")
        ui.target = self
        menu.addItem(ui)

        let folder = NSMenuItem(title: "Папка записей", action: #selector(openFolder), keyEquivalent: "")
        folder.target = self
        menu.addItem(folder)

        menu.addItem(.separator())

        let setup = NSMenuItem(title: "Создать аудиоустройства", action: #selector(makeDevicesFromMenu), keyEquivalent: "")
        setup.target = self
        menu.addItem(setup)

        menu.addItem(deviceSubmenu(title: "Микрофон", devices: CA.inputs(),
                                   current: micName, action: #selector(pickMic(_:))))
        menu.addItem(deviceSubmenu(title: "Слушать через", devices: CA.outputs().filter { CA.name($0) != "Podslushka Out" },
                                   current: outName, action: #selector(pickOut(_:))))

        if CA.name(CA.defaultOutput) == "Podslushka Out" {
            let fix = NSMenuItem(title: "Вернуть звук на \(outName)",
                                 action: #selector(restoreOutputFromMenu), keyEquivalent: "")
            fix.target = self
            menu.addItem(fix)
            menu.addItem(.separator())
        }

        let pan = NSMenuItem(title: "Показывать панель", action: #selector(togglePanel), keyEquivalent: "")
        pan.target = self
        pan.state = panelVisible ? .on : .off
        menu.addItem(pan)

        let dock = NSMenuItem(title: "Показывать в Dock", action: #selector(toggleDock), keyEquivalent: "")
        dock.target = self
        dock.state = inDock ? .on : .off
        menu.addItem(dock)

        let sw = NSMenuItem(title: "Переключать звук самому", action: #selector(toggleAuto), keyEquivalent: "")
        sw.target = self
        sw.state = autoSwitch ? .on : .off
        menu.addItem(sw)

        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Выйти", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        dbg("меню собрано, пунктов \(menu.numberOfItems)")
    }

    func deviceSubmenu(title: String, devices: [AudioObjectID], current: String,
                       action: Selector) -> NSMenuItem {
        let root = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        let sub = NSMenu()
        for d in devices {
            let n = CA.name(d)
            if n.hasPrefix("Podslushka") { continue }
            let mi = NSMenuItem(title: n, action: action, keyEquivalent: "")
            mi.target = self
            mi.state = (n == current) ? .on : .off
            sub.addItem(mi)
        }
        if sub.items.isEmpty {
            let mi = NSMenuItem(title: "устройств не видно", action: nil, keyEquivalent: "")
            mi.isEnabled = false
            sub.addItem(mi)
        }
        root.submenu = sub
        return root
    }

    @objc func pickMic(_ s: NSMenuItem) { micName = s.title; rebuildMenu() }
    @objc func pickOut(_ s: NSMenuItem) { outName = s.title; rebuildMenu() }
    @objc func toggleAuto() { autoSwitch = !autoSwitch; rebuildMenu() }
    @objc func toggleDock() { inDock = !inDock; rebuildMenu() }
    @objc func quit() {
        if recording {
            let a = NSAlert()
            a.messageText = "Запись ещё идёт"
            a.informativeText = "Выйти и потерять текущую запись?"
            a.addButton(withTitle: "Остановить и выйти")
            a.addButton(withTitle: "Отмена")
            if a.runModal() != .alertFirstButtonReturn { return }
            run(["stop"]) { _ in NSApp.terminate(nil) }
            return
        }
        NSApp.terminate(nil)
    }

    // MARK: действия

    @objc func toggle() { dbg("нажато: запись/стоп"); recording ? stop() : start() }

    /// Прежний выход запоминается на диске: если приложение закроют во время
    /// записи, вернуть звук сможет следующий запуск. Пока выбран составной
    /// выход, клавиши громкости не работают — macOS не умеет крутить громкость
    /// агрегата, поэтому зависший выход выглядит как поломка звука.
    func rememberOutput(_ id: AudioObjectID) {
        savedOutput = id
        if let uid = CA.uid(id) { defaults.set(uid, forKey: "prevOutputUID") }
    }

    @discardableResult
    func restoreOutputNow() -> String? {
        var target: AudioObjectID? = savedOutput
        if target == nil, let uid = defaults.string(forKey: "prevOutputUID") {
            target = CA.allDevices().first { CA.uid($0) == uid }
        }
        if target == nil, let byName = CA.find(name: outName) { target = byName }
        guard let id = target else { return nil }
        CA.defaultOutput = id
        savedOutput = nil
        defaults.removeObject(forKey: "prevOutputUID")
        dbg("выход возвращён на \(CA.name(id))")
        return CA.name(id)
    }

    /// Звук мог остаться на составном выходе после падения или закрытия
    /// приложения во время записи. Если запись не идёт — возвращаем.
    func fixStuckOutput() {
        guard externalPID == nil else { return }
        let cur = CA.defaultOutput
        guard CA.name(cur) == "Podslushka Out" else { return }
        if let name = restoreOutputNow() {
            dbg("выход был залипшим, вернули на \(name)")
        }
    }

    @objc func restoreOutputFromMenu() {
        if let name = restoreOutputNow() {
            alert("Звук возвращён", "Выход снова \(name). Клавиши громкости работают.")
        } else {
            alert("Некуда возвращать", "Не нашёл прежнее устройство. Выбери выход вручную: Option и клик по значку звука.")
        }
    }

    func start() {
        guard CA.find(name: "Podslushka In") != nil else {
            return alert("Нет устройства Podslushka In",
                         "Нажми «Создать аудиоустройства» — оно соберёт Podslushka In и Podslushka Out.")
        }
        if autoSwitch, let target = CA.find(name: "Podslushka Out") {
            rememberOutput(CA.defaultOutput)
            CA.defaultOutput = target
        }
        run(["start"]) { [weak self] ok in
            guard let self else { return }
            if ok {
                self.recording = true
                self.startedAt = Date()
            } else {
                self.restoreOutput()
                self.alert("Запись не началась", "Посмотри /tmp/podslushka/ffmpeg.log")
            }
            self.rebuildMenu()
            self.redrawIcon()
        }
    }

    func stop() {
        busy = true
        recording = false
        startedAt = nil
        rebuildMenu()
        redrawIcon()
        run(["stop"]) { [weak self] _ in
            guard let self else { return }
            self.busy = false
            self.restoreOutput()
            self.rebuildMenu()
            self.redrawIcon()
        }
    }

    func restoreOutput() {
        if savedOutput != nil || defaults.string(forKey: "prevOutputUID") != nil {
            restoreOutputNow()
        }
    }

    @objc func openUI() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [scripts.appendingPathComponent("podslushka-ui").path]
        try? p.run()
    }

    @objc func openFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath:
            NSString(string: ProcessInfo.processInfo.environment["PODSLUSHKA_DIR"]
                     ?? "~/Documents/Подслушка").expandingTildeInPath))
    }

    @objc func makeDevicesFromMenu() {
        makeDevices(silent: false)
    }

    @discardableResult
    func makeDevices(silent: Bool) -> Bool {
        func say(_ title: String, _ body: String) {
            if silent {
                print(title)
                print(body)
            } else {
                alert(title, body)
            }
        }

        guard let bh = CA.find(name: "BlackHole 2ch"), let bhUID = CA.uid(bh) else {
            say("BlackHole не найден",
                "Поставь: brew install --cask blackhole-2ch, потом sudo killall coreaudiod.")
            return false
        }
        guard let mic = CA.find(name: micName), let micUID = CA.uid(mic) else {
            say("Микрофон не найден",
                "Сейчас выбран «\(micName)». Задать другой: --mic \"Имя\"")
            return false
        }
        guard let out = CA.find(name: outName), let outUID = CA.uid(out) else {
            say("Выход не найден",
                "Сейчас выбран «\(outName)». Задать другой: --out \"Имя\"")
            return false
        }

        var problems: [String] = []
        if let e = CA.createAggregate(name: "Podslushka In", uid: "com.podslushka.in",
                                      subUIDs: [micUID, bhUID], master: micUID,
                                      driftOn: [bhUID], stacked: false) {
            problems.append("Podslushka In: \(e)")
        }
        if let e = CA.createAggregate(name: "Podslushka Out", uid: "com.podslushka.out",
                                      subUIDs: [outUID, bhUID], master: outUID,
                                      driftOn: [bhUID], stacked: true) {
            problems.append("Podslushka Out: \(e)")
        }

        if problems.isEmpty {
            say("Устройства готовы",
                "Podslushka In — \(micName) + BlackHole.\n"
                + "Podslushka Out — \(outName) + BlackHole.\n\n"
                + "В приложении звонка выбери динамик Podslushka Out, микрофон оставь обычный.")
        } else {
            say("Не всё вышло", problems.joined(separator: "\n"))
        }
        if !silent { rebuildMenu() }
        return problems.isEmpty
    }

    // MARK: запуск скрипта

    func run(_ args: [String], done: @escaping (Bool) -> Void) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [scripts.appendingPathComponent("podslushka").path] + args
        p.terminationHandler = { proc in
            DispatchQueue.main.async { done(proc.terminationStatus == 0) }
        }
        do { try p.run() } catch { DispatchQueue.main.async { done(false) } }
    }

    func alert(_ title: String, _ text: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.runModal()
    }
}

extension App: NSWindowDelegate {
    func windowDidMove(_ n: Notification) {
        guard let w = n.object as? NSWindow, w === panel else { return }
        defaults.set(NSStringFromPoint(w.frame.origin), forKey: "panelPos")
    }
}

extension App: NSMenuDelegate {
    // Делегат только следит за тем, открыто ли меню. Состав пунктов здесь не
    // трогается: перестройка в момент открытия — как раз то, из-за чего меню
    // не показывалось.
    func menuWillOpen(_ menu: NSMenu) {
        menuOpen = true
        dbg("меню открывается, пунктов \(menu.numberOfItems)")
    }
    func menuDidClose(_ menu: NSMenu) { menuOpen = false; dbg("меню закрыто") }
}

extension App {
    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        rebuildMenu()
        let copy = NSMenu()
        for i in menu.items {
            let c = NSMenuItem(title: i.title, action: i.action, keyEquivalent: "")
            c.target = i.target
            c.state = i.state
            c.isEnabled = i.isEnabled
            if let sub = i.submenu {
                let subCopy = NSMenu()
                for si in sub.items {
                    let sc = NSMenuItem(title: si.title, action: si.action, keyEquivalent: "")
                    sc.target = si.target
                    sc.state = si.state
                    subCopy.addItem(sc)
                }
                c.submenu = subCopy
            }
            copy.addItem(i.isSeparatorItem ? .separator() : c)
        }
        return copy
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.run()
