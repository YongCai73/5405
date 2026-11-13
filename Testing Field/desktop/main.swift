import AppKit
import AVFoundation
import Foundation
import UniformTypeIdentifiers
import WebKit

final class BackendProcess {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let diagnostics = Pipe()
    private let readerQueue = DispatchQueue(label: "com.meetingcopilot.desktop.backend")
    private var buffered = Data()
    private var pending = Set<String>()
    private var ready = false
    private var closing = false
    var onReply: (([String: Any]) -> Void)?
    var onExit: (() -> Void)?

    func start(backendRoot: URL) throws {
        let python = backendRoot.appendingPathComponent(".venv/bin/python")
        let script = backendRoot.appendingPathComponent("desktop/bridge.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.isReadableFile(atPath: script.path) else {
            throw CocoaError(.fileNoSuchFile)
        }
        let override = ProcessInfo.processInfo.environment["MEETING_COPILOT_DESKTOP_DATA_DIR"]
        let support = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true)
        let dataRoot = override.flatMap { $0.hasPrefix("/") ? $0 : nil }
            ?? support.appendingPathComponent("Meeting Copilot/Workspace").path
        var environment = ProcessInfo.processInfo.environment
        var arguments = ["-u", script.path, "--data-dir", dataRoot]
        if let config = environment["MEETING_COPILOT_DESKTOP_CONFIG"], config.hasPrefix("/") {
            arguments += ["--config", config]
        }
        process.executableURL = python
        process.arguments = arguments
        process.currentDirectoryURL = backendRoot
        environment["PYTHONPATH"] = backendRoot.appendingPathComponent("src").path
        environment.removeValue(forKey: "PYTHONHOME")
        process.environment = environment
        process.standardInput = input
        process.standardOutput = output
        process.standardError = diagnostics
        diagnostics.fileHandleForReading.readabilityHandler = { handle in
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            self?.readerQueue.async { [weak self] in self?.receive(data) }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.didExit() }
        }
        try process.run()
        ready = true
    }

    func send(id: String, action: String, payload: [String: Any]) {
        guard ready && !closing else {
            fail(id: id, message: "The meeting backend is unavailable. Reopen the app to retry.")
            return
        }
        do {
            let request: [String: Any] = ["id": id, "action": action, "payload": payload]
            var bytes = try JSONSerialization.data(withJSONObject: request)
            bytes.append(10)
            pending.insert(id)
            try input.fileHandleForWriting.write(contentsOf: bytes)
        } catch {
            pending.remove(id)
            fail(id: id, message: "Could not send the request to the meeting backend.")
        }
    }

    func close() -> Bool {
        guard ready, process.isRunning else { return false }
        if !closing {
            closing = true
            try? input.fileHandleForWriting.close()
            DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
                guard let self, self.process.isRunning else { return }
                self.process.terminate()
            }
        }
        return true
    }

    private func receive(_ data: Data) {
        buffered.append(data)
        while let newline = buffered.firstIndex(of: 10) {
            let line = buffered.prefix(upTo: newline)
            buffered.removeSubrange(...newline)
            guard let reply = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let id = reply["id"] as? String else { continue }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.pending.remove(id) != nil else { return }
                self.onReply?(reply)
            }
        }
    }

    private func didExit() {
        ready = false
        for id in pending { fail(id: id, message: "The meeting backend stopped. Reopen the app to retry.") }
        pending.removeAll()
        onExit?()
    }

    private func fail(id: String, message: String) {
        onReply?(["id": id, "ok": false, "error": message])
    }
}

final class DesktopApp: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    private let backend = BackendProcess()
    private var window: NSWindow!
    private var web: WKWebView!
    private var indexURL: URL!
    private var waitingForBackend = false
    private let transferQueue = DispatchQueue(label: "com.meetingcopilot.desktop.transfer")
    private var transfers: [String: FileTransfer] = [:]
    private let actions = Set([
        "bootstrap", "meeting", "demo", "create", "ask", "summary", "export", "importAudio",
        "devices", "startRecording", "stopRecording",
    ])

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let ui = Bundle.main.resourceURL?.appendingPathComponent("ui", isDirectory: true),
              let locator = Bundle.main.url(forResource: "backend-root", withExtension: "txt"),
              let location = try? String(contentsOf: locator, encoding: .utf8) else {
            showLaunchError("The app bundle is missing resources. Rebuild it from the desktop folder.")
            return
        }

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "copilot")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        indexURL = ui.appendingPathComponent("index.html").standardizedFileURL

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Meeting Copilot"
        window.contentMinSize = NSSize(width: 980, height: 650)
        window.contentView = web
        window.center()
        installMenu()

        backend.onReply = { [weak self] reply in self?.reply(reply) }
        backend.onExit = { [weak self] in
            guard self?.waitingForBackend == true else { return }
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        do {
            try backend.start(backendRoot: URL(fileURLWithPath: location.trimmingCharacters(in: .newlines), isDirectory: true))
        } catch {
            showLaunchError("The local Python backend could not start. Check the project's virtual environment and rebuild the app.")
            return
        }

        web.loadFileURL(indexURL, allowingReadAccessTo: ui)
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        waitingForBackend = backend.close()
        return waitingForBackend ? .terminateLater : .terminateNow
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let allowed = navigationAction.request.url?.standardizedFileURL == indexURL
            && navigationAction.targetFrame?.isMainFrame == true
        decisionHandler(allowed ? .allow : .cancel)
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "copilot", message.frameInfo.isMainFrame,
              message.frameInfo.request.url?.standardizedFileURL == indexURL,
              let request = message.body as? [String: Any],
              let id = request["id"] as? String, !id.isEmpty, id.count <= 80,
              let action = request["action"] as? String, actions.contains(action),
              let payload = request["payload"] as? [String: Any] else { return }

        switch action {
        case "startRecording":
            if AVCaptureDevice.authorizationStatus(for: .audio) == .authorized {
                backend.send(id: id, action: action, payload: payload)
            } else {
                AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
                    DispatchQueue.main.async {
                        if allowed {
                            self?.backend.send(id: id, action: action, payload: payload)
                        } else {
                            self?.reply(["id": id, "ok": false,
                                         "error": "Allow Meeting Copilot to use the microphone in System Settings › Privacy & Security › Microphone."])
                        }
                    }
                }
            }
        case "importAudio":
            let picker = NSOpenPanel()
            picker.title = "Import meeting audio"
            picker.allowedContentTypes = [.audio, .movie]
            picker.allowsMultipleSelection = false
            picker.beginSheetModal(for: window) { [weak self] response in
                self?.sendPickedFile(picker.url, response: response, id: id, action: action, payload: payload)
            }
        case "export":
            let picker = NSSavePanel()
            picker.title = "Export meeting"
            picker.allowedContentTypes = [.json]
            picker.nameFieldStringValue = "meeting-export.json"
            picker.beginSheetModal(for: window) { [weak self] response in
                self?.sendPickedFile(picker.url, response: response, id: id, action: action, payload: payload)
            }
        default:
            backend.send(id: id, action: action, payload: payload)
        }
    }

    private func sendPickedFile(_ url: URL?, response: NSApplication.ModalResponse, id: String,
                                action: String, payload: [String: Any]) {
        guard response == .OK, let url else {
            reply(["id": id, "ok": true, "result": ["cancelled": true]])
            return
        }
        let meeting = payload["meetingId"] as? String ?? ""
        transferQueue.async { [weak self] in
            do {
                let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: false,
                                                        attributes: [.posixPermissions: 0o700])
                let temporary = folder.appendingPathComponent(action == "importAudio" ? url.lastPathComponent : "meeting.json")
                do {
                    if action == "importAudio" { try FileManager.default.copyItem(at: url, to: temporary) }
                } catch {
                    try? FileManager.default.removeItem(at: folder)
                    throw error
                }
                let transfer = FileTransfer(temporary: temporary, destination: action == "export" ? url : nil)
                DispatchQueue.main.async { [weak self] in
                    guard let self else { return }
                    self.transfers[id] = transfer
                    self.backend.send(id: id, action: action, payload: ["meetingId": meeting, "path": temporary.path])
                }
            } catch {
                DispatchQueue.main.async { [weak self] in
                    self?.reply(["id": id, "ok": false, "error": "Could not open the selected file."])
                }
            }
        }
    }

    private struct FileTransfer {
        let temporary: URL
        let destination: URL?
    }

    private func reply(_ message: [String: Any]) {
        guard let id = message["id"] as? String, let transfer = transfers.removeValue(forKey: id) else {
            deliver(message)
            return
        }
        transferQueue.async { [weak self] in
            var result = message
            if let destination = transfer.destination, message["ok"] as? Bool == true {
                do {
                    let contents = try Data(contentsOf: transfer.temporary)
                    try contents.write(to: destination, options: .atomic)
                    result["result"] = ["path": destination.path]
                } catch {
                    result = ["id": id, "ok": false, "error": "Could not save the export to the selected folder."]
                }
            }
            try? FileManager.default.removeItem(at: transfer.temporary.deletingLastPathComponent())
            let reply = result
            DispatchQueue.main.async { [weak self] in self?.deliver(reply) }
        }
    }

    private func deliver(_ message: [String: Any]) {
        web.callAsyncJavaScript("window.copilotReceive(message)", arguments: ["message": message],
                                in: nil, in: .page, completionHandler: nil)
    }

    private func showLaunchError(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Meeting Copilot couldn't open"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }

    private func installMenu() {
        let mainMenu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Meeting Copilot", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Meeting Copilot", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        for (name, selector, shortcut) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"),
                                            ("Copy", "copy:", "c"), ("Paste", "paste:", "v"),
                                            ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: name, action: Selector(selector), keyEquivalent: shortcut)
        }
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)
        NSApp.mainMenu = mainMenu
    }
}

let app = NSApplication.shared
let delegate = DesktopApp()
app.setActivationPolicy(.regular)
app.delegate = delegate
app.run()
