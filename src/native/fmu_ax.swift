import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation
import ScreenCaptureKit
import Vision

func crc32(_ text: String) -> String {
    var crc: UInt32 = 0xFFFF_FFFF
    for byte in text.utf8 {
        crc ^= UInt32(byte)
        for _ in 0..<8 {
            crc = (crc >> 1) ^ (0xEDB8_8320 & (0 &- (crc & 1)))
        }
    }
    return String(format: "%08x", ~crc)
}

struct NodeRecord: Codable {
    let uid: String
    let structKey: String
    let role: String
    let label: String
    let value: String
    let enabled: Bool
    let x: Int
    let y: Int
    let w: Int
    let h: Int

    var compactLine: String {
        var s = "\(uid) \(role)"
        if !label.isEmpty { s += " \"\(label)\"" }
        if !value.isEmpty && value != label { s += " val=\"\(value)\"" }
        if !enabled { s += " [disabled]" }
        return s
    }

    var bodyWithoutUid: String {
        var s = role
        if !label.isEmpty { s += " \"\(label)\"" }
        if !value.isEmpty && value != label { s += " val=\"\(value)\"" }
        if !enabled { s += " [disabled]" }
        return s
    }
}

struct StepSpec: Codable {
    let op: String?
    let action: String?
    let uid: String?
    let ref: String?
    let target: String?
    let selector: String?
    let index: Int?
    let role: String?
    let name: String?
    let key: String?
    let value: String?
    let path: [String]?
    let ms: Int?
    let x: Int?
    let y: Int?
    let optional: Bool?

    var effectiveOp: String {
        let raw = (op ?? action ?? "").lowercased()
        if raw == "press" {
            if key != nil && uid == nil && ref == nil && target == nil && name == nil && index == nil {
                return "key"
            }
            return "click"
        }
        if raw == "fill" {
            return "set"
        }
        if raw == "hover" || raw == "cursor" || raw == "point" {
            return "move"
        }
        return raw
    }

    var effectiveUid: String? {
        for cand in [uid, ref, target, selector, name] {
            if let c = cand, c.hasPrefix("@a") || c.hasPrefix("@u") || c.hasPrefix("@e") {
                if c.hasPrefix("@u") || c.hasPrefix("@e") {
                    return "@a" + String(c.dropFirst(2))
                }
                return c
            }
        }
        return nil
    }

    var effectiveName: String? {
        return name ?? target ?? ref ?? selector
    }
}

struct BatchRequest: Codable {
    let agent: String?
    let cursor: String?
    let app: String?
    let window: String?
    let query: String?
    let steps: [StepSpec]?
    let diff: Bool?
    let settle: Bool?
    let reset: Bool?
    let showDocs: Bool?
    let ocr: Bool?
    let getCursors: Bool?

    var effectiveAgent: String {
        let raw = (agent ?? cursor ?? "default").trimmingCharacters(in: .whitespacesAndNewlines)
        return raw.isEmpty ? "default" : raw
    }
}

struct VirtualCursorState: Codable {
    let agent: String
    let app: String
    let window: String
    let uid: String
    let label: String
    let x: Int
    let y: Int
    let lastOp: String
}

struct TargetApp {
    let pid: pid_t
    let name: String
    let windowID: CGWindowID
}

final class EngineState {
    static let shared = EngineState()

    let lock = NSLock()
    var nextUidCounter: Int = 1
    var cfHashToUid: [UInt: String] = [:]
    var structKeyToUid: [String: String] = [:]
    var uidToElement: [String: AXUIElement] = [:]
    var uidToRecord: [String: NodeRecord] = [:]
    var uidOrder: [String] = []
    let maxRefCache = 4000

    var windowBaselines: [String: [NodeRecord]] = [:]
    var windowCrc: [String: String] = [:]
    var latestWindowNodes: [String: [NodeRecord]] = [:]
    var cachedAppWindows: [pid_t: (AXUIElement, String)] = [:]
    var appCache: [String: TargetApp] = [:]
    var appPidByKey: [String: pid_t] = [:]
    var wokenPids: Set<pid_t> = []
    var documentedApps: Set<String> = []
    var agentCursors: [String: VirtualCursorState] = [:]

    func reset(agent: String? = nil) {
        if let ag = agent, !ag.isEmpty && ag != "all" {
            let prefix = "\(ag)|"
            windowBaselines = windowBaselines.filter { !$0.key.hasPrefix(prefix) }
            windowCrc = windowCrc.filter { !$0.key.hasPrefix(prefix) }
            documentedApps = Set(documentedApps.filter { !$0.hasPrefix(prefix) })
            agentCursors.removeValue(forKey: ag)
            return
        }
        nextUidCounter = 1
        cfHashToUid.removeAll()
        structKeyToUid.removeAll()
        uidToElement.removeAll()
        uidToRecord.removeAll()
        uidOrder.removeAll()
        windowBaselines.removeAll()
        windowCrc.removeAll()
        latestWindowNodes.removeAll()
        cachedAppWindows.removeAll()
        appCache.removeAll()
        appPidByKey.removeAll()
        wokenPids.removeAll()
        documentedApps.removeAll()
        agentCursors.removeAll()
    }

    func registerElement(_ el: AXUIElement, structKey: String, usedInPass: inout Set<String>) -> String {
        let h = CFHash(el)
        let uid: String
        if let existing = cfHashToUid[h], !usedInPass.contains(existing) {
            uid = existing
        } else if let existingByStruct = structKeyToUid[structKey],
                  !usedInPass.contains(existingByStruct) {
            uid = existingByStruct
            cfHashToUid[h] = uid
        } else {
            uid = "@a\(nextUidCounter)"
            nextUidCounter += 1
            cfHashToUid[h] = uid
        }
        usedInPass.insert(uid)
        structKeyToUid[structKey] = uid
        uidToElement[uid] = el
        uidOrder.append(uid)
        if uidOrder.count > maxRefCache {
            let evicted = uidOrder.removeFirst()
            uidToElement.removeValue(forKey: evicted)
            uidToRecord.removeValue(forKey: evicted)
        }
        return uid
    }
}

let batchAttrNames: [String] = [
    kAXRoleAttribute as String,
    kAXTitleAttribute as String,
    kAXDescriptionAttribute as String,
    kAXValueAttribute as String,
    kAXEnabledAttribute as String,
    kAXPositionAttribute as String,
    kAXSizeAttribute as String
]
let batchAttrsCF = batchAttrNames as CFArray

struct FastAXAttrs {
    var role: String = ""
    var title: String = ""
    var desc: String = ""
    var value: String = ""
    var enabled: Bool = true
    var x: Int = 0
    var y: Int = 0
    var w: Int = 0
    var h: Int = 0
}

func cleanText(_ anyVal: AnyObject?) -> String {
    guard let val = anyVal else { return "" }
    if CFGetTypeID(val) == CFNullGetTypeID() { return "" }
    if let s = val as? String {
        return s.replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
            .trimmingCharacters(in: .whitespaces)
    }
    if let n = val as? NSNumber {
        return n.stringValue
    }
    return ""
}

func axString(_ el: AXUIElement, _ attr: String) -> String {
    var ref: CFTypeRef?
    guard AXUIElementCopyAttributeValue(el, attr as CFString, &ref) == .success, let val = ref else { return "" }
    return cleanText(val)
}

func axChildren(_ el: AXUIElement) -> [AXUIElement] {
    var ref: CFTypeRef?
    guard AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &ref) == .success,
          let arr = ref as? [AXUIElement] else { return [] }
    return arr
}

func readBatchedAttrs(_ el: AXUIElement) -> FastAXAttrs {
    var out = FastAXAttrs()
    var valuesRef: CFArray?
    let err = AXUIElementCopyMultipleAttributeValues(el, batchAttrsCF, [], &valuesRef)
    if err == .success, let cfArr = valuesRef, CFArrayGetCount(cfArr) == batchAttrNames.count {
        func item(_ idx: Int) -> AnyObject? {
            guard let ptr = CFArrayGetValueAtIndex(cfArr, idx) else { return nil }
            let obj = Unmanaged<AnyObject>.fromOpaque(ptr).takeUnretainedValue()
            if CFGetTypeID(obj) == AXValueGetTypeID() {
                let axv = obj as! AXValue
                if AXValueGetType(axv) == .axError { return nil }
            }
            return obj
        }
        let rawRole = cleanText(item(0))
        out.role = rawRole.hasPrefix("AX") ? String(rawRole.dropFirst(2)).lowercased() : rawRole.lowercased()
        out.title = cleanText(item(1))
        out.desc = cleanText(item(2))
        out.value = cleanText(item(3))
        if let b = item(4) as? Bool {
            out.enabled = b
        }
        if let posObj = item(5), CFGetTypeID(posObj) == AXValueGetTypeID() {
            var pt = CGPoint.zero
            if AXValueGetValue(posObj as! AXValue, .cgPoint, &pt) {
                out.x = Int(pt.x)
                out.y = Int(pt.y)
            }
        }
        if let szObj = item(6), CFGetTypeID(szObj) == AXValueGetTypeID() {
            var sz = CGSize.zero
            if AXValueGetValue(szObj as! AXValue, .cgSize, &sz) {
                out.w = Int(sz.width)
                out.h = Int(sz.height)
            }
        }
        return out
    }
    let rawRole = axString(el, kAXRoleAttribute)
    out.role = rawRole.hasPrefix("AX") ? String(rawRole.dropFirst(2)).lowercased() : rawRole.lowercased()
    out.title = axString(el, kAXTitleAttribute)
    out.desc = axString(el, kAXDescriptionAttribute)
    out.value = axString(el, kAXValueAttribute)
    return out
}

let interactiveRoles: Set<String> = [
    "button", "textfield", "textarea", "searchfield", "checkbox", "radiobutton",
    "combobox", "popupbutton", "menuitem", "tab", "link", "statictext", "slider",
    "row", "cell", "incrementor", "switch", "disclosuretriangle", "sheet", "dialog"
]

func wakeLazyAXIfNeeded(_ pid: pid_t, appElement: AXUIElement) {
    let state = EngineState.shared
    guard !state.wokenPids.contains(pid) else { return }
    state.wokenPids.insert(pid)
    _ = AXUIElementSetAttributeValue(appElement, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    _ = AXUIElementSetAttributeValue(appElement, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
}

func walkAXTree(_ root: AXUIElement, winKey: String, query: String? = nil, maxElements: Int = 250) -> ([NodeRecord], [AXUIElement]) {
    var records: [NodeRecord] = []
    var elements: [AXUIElement] = []
    var stack: [AXUIElement] = [root]
    var visited = 0
    var structCounts: [String: Int] = [:]
    var usedInPass: Set<String> = []
    let q = query?.lowercased()
    let state = EngineState.shared

    while !stack.isEmpty && records.count < maxElements && visited < 2200 {
        let el = stack.removeFirst()
        visited += 1
        let attrs = readBatchedAttrs(el)
        let role = attrs.role
        if role == "menubar" || role == "menubaritem" {
            continue
        }
        var label = !attrs.title.isEmpty ? attrs.title : attrs.desc
        if label.isEmpty && (role == "textfield" || role == "textarea" || role == "searchfield") {
            label = axString(el, "AXPlaceholderValue")
        }
        let val = attrs.value

        if interactiveRoles.contains(role) && (!label.isEmpty || !val.isEmpty || role == "textfield" || role == "searchfield" || role == "textarea") {
            let matchesQuery: Bool
            if let q = q, !q.isEmpty {
                matchesQuery = label.lowercased().contains(q) || val.lowercased().contains(q) || role.contains(q)
            } else {
                matchesQuery = true
            }
            if matchesQuery {
                let baseKey = "\(winKey)|\(role)|\(label)"
                let occ = (structCounts[baseKey] ?? 0) + 1
                structCounts[baseKey] = occ
                let structKey = "\(baseKey)#\(occ)"
                let uid = state.registerElement(el, structKey: structKey, usedInPass: &usedInPass)
                let rec = NodeRecord(
                    uid: uid,
                    structKey: structKey,
                    role: role,
                    label: label,
                    value: val,
                    enabled: attrs.enabled,
                    x: attrs.x,
                    y: attrs.y,
                    w: attrs.w,
                    h: attrs.h
                )
                state.uidToRecord[uid] = rec
                records.append(rec)
                elements.append(el)
            }
        }
        let children = axChildren(el)
        if !children.isEmpty {
            stack.insert(contentsOf: children, at: 0)
        }
    }
    return (records, elements)
}

func isProcessAndAXAlive(_ pid: pid_t) -> Bool {
    guard pid > 0, kill(pid, 0) == 0 else { return false }
    guard let running = NSRunningApplication(processIdentifier: pid), !running.isTerminated else {
        return false
    }
    let appEl = AXUIElementCreateApplication(pid)
    var roleRef: CFTypeRef?
    return AXUIElementCopyAttributeValue(appEl, kAXRoleAttribute as CFString, &roleRef) == .success
}

@_silgen_name("_AXUIElementCreateWithRemoteToken")
func _AXUIElementCreateWithRemoteToken(_ data: CFData) -> Unmanaged<AXUIElement>?

func offSpaceWindows(_ pid: pid_t) -> [AXUIElement] {
    var out: [AXUIElement] = []
    var token = Data(count: 20)
    token.replaceSubrange(0..<4, with: withUnsafeBytes(of: pid) { Data($0) })
    token.replaceSubrange(4..<8, with: withUnsafeBytes(of: Int32(0)) { Data($0) })
    token.replaceSubrange(8..<12, with: withUnsafeBytes(of: Int32(0x636f636f)) { Data($0) })
    for id in UInt64(1)..<1500 {
        token.replaceSubrange(12..<20, with: withUnsafeBytes(of: id) { Data($0) })
        if let el = _AXUIElementCreateWithRemoteToken(token as CFData)?.takeRetainedValue(),
           axString(el, kAXRoleAttribute) == "AXWindow" {
            out.append(el)
        }
    }
    return out
}

func hasReadyAXWindow(_ pid: pid_t) -> Bool {
    let appEl = AXUIElementCreateApplication(pid)
    wakeLazyAXIfNeeded(pid, appElement: appEl)
    var ref: CFTypeRef?
    var wins: [AXUIElement] = []
    if AXUIElementCopyAttributeValue(appEl, kAXWindowsAttribute as CFString, &ref) == .success,
       let wArr = ref as? [AXUIElement], !wArr.isEmpty {
        wins = wArr
    } else if AXUIElementCopyAttributeValue(appEl, kAXMainWindowAttribute as CFString, &ref) == .success,
              let mainWin = ref, axString(mainWin as! AXUIElement, kAXRoleAttribute) == "AXWindow" {
        wins = [mainWin as! AXUIElement]
    }
    if wins.isEmpty {
        wins = offSpaceWindows(pid)
    }
    return wins.contains(where: { !axChildren($0).isEmpty })
}

func findApp(nameOrBundle: String, launchIfNeeded: Bool = true) -> TargetApp? {
    let lower = nameOrBundle.lowercased()
    let state = EngineState.shared
    if let cached = state.appCache[lower] {
        if kill(cached.pid, 0) == 0,
           let running = NSRunningApplication(processIdentifier: cached.pid),
           !running.isTerminated {
            if let cachedWin = state.cachedAppWindows[cached.pid],
               axString(cachedWin.0, kAXRoleAttribute) == "AXWindow" {
                return cached
            }
            if hasReadyAXWindow(cached.pid) {
                return cached
            }
        }
        state.appCache.removeValue(forKey: lower)
        state.cachedAppWindows.removeValue(forKey: cached.pid)
    }

    if let infoList = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] {
        for entry in infoList {
            if let ownerName = entry[kCGWindowOwnerName as String] as? String,
               let pid = entry[kCGWindowOwnerPID as String] as? pid_t,
               ownerName.lowercased() == lower {
                let wid = (entry[kCGWindowNumber as String] as? UInt32) ?? 0
                let layer = (entry[kCGWindowLayer as String] as? Int) ?? 0
                if layer == 0 && wid != 0 && isProcessAndAXAlive(pid) {
                    let found = TargetApp(pid: pid, name: ownerName, windowID: wid)
                    if hasReadyAXWindow(pid) || !launchIfNeeded {
                        state.appCache[lower] = found
                        return found
                    }
                }
            }
        }
    }
    let ws = NSWorkspace.shared
    if let match = ws.runningApplications.first(where: {
        (($0.localizedName?.lowercased() == lower) || ($0.bundleIdentifier?.lowercased() == lower))
            && !$0.isTerminated
    }) {
        let pid = match.processIdentifier
        if isProcessAndAXAlive(pid) && (hasReadyAXWindow(pid) || !launchIfNeeded) {
            let found = TargetApp(pid: pid, name: match.localizedName ?? nameOrBundle, windowID: 0)
            if hasReadyAXWindow(pid) {
                state.appCache[lower] = found
            }
            return found
        }
    }
    if launchIfNeeded {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-g", "-a", nameOrBundle]
        try? p.run()
        p.waitUntilExit()
        if p.terminationStatus != 0 {
            return nil
        }
        var lastFound: TargetApp?
        for _ in 0..<45 {
            Thread.sleep(forTimeInterval: 0.035)
            if let found = findApp(nameOrBundle: nameOrBundle, launchIfNeeded: false) {
                lastFound = found
                if hasReadyAXWindow(found.pid) {
                    state.appCache[lower] = found
                    return found
                }
            }
        }
        return lastFound
    }
    return nil
}

func getTargetRoot(_ app: TargetApp, windowFilter: String? = nil) -> (AXUIElement, String) {
    let state = EngineState.shared
    if windowFilter == nil, let cached = state.cachedAppWindows[app.pid] {
        if axString(cached.0, kAXRoleAttribute) == "AXWindow" && !axChildren(cached.0).isEmpty {
            return cached
        }
        state.cachedAppWindows.removeValue(forKey: app.pid)
    }
    let appEl = AXUIElementCreateApplication(app.pid)
    wakeLazyAXIfNeeded(app.pid, appElement: appEl)
    var ref: CFTypeRef?
    var wins: [AXUIElement] = []
    if AXUIElementCopyAttributeValue(appEl, kAXWindowsAttribute as CFString, &ref) == .success,
       let wArr = ref as? [AXUIElement], !wArr.isEmpty {
        wins = wArr
    } else if AXUIElementCopyAttributeValue(appEl, kAXMainWindowAttribute as CFString, &ref) == .success,
              let mainWin = ref, axString(mainWin as! AXUIElement, kAXRoleAttribute) == "AXWindow" {
        wins = [mainWin as! AXUIElement]
    } else if AXUIElementCopyAttributeValue(appEl, kAXFocusedWindowAttribute as CFString, &ref) == .success,
              let focWin = ref, axString(focWin as! AXUIElement, kAXRoleAttribute) == "AXWindow" {
        wins = [focWin as! AXUIElement]
    }
    if wins.isEmpty || !wins.contains(where: { !axChildren($0).isEmpty }) || (windowFilter != nil && !wins.contains(where: { axString($0, kAXTitleAttribute).lowercased().contains(windowFilter!.lowercased()) })) {
        let extra = offSpaceWindows(app.pid)
        if !extra.isEmpty {
            wins.append(contentsOf: extra)
        }
    }
    if !wins.isEmpty {
        if let wf = windowFilter?.lowercased(), !wf.isEmpty {
            if let matched = wins.first(where: { axString($0, kAXTitleAttribute).lowercased().contains(wf) && !axChildren($0).isEmpty })
                ?? wins.first(where: { axString($0, kAXTitleAttribute).lowercased().contains(wf) }) {
                let wTitle = axString(matched, kAXTitleAttribute)
                let res = (matched, wTitle.isEmpty ? app.name : wTitle)
                state.cachedAppWindows[app.pid] = res
                return res
            }
        }
        let first = wins.first(where: { !axChildren($0).isEmpty }) ?? wins[0]
        let wTitle = axString(first, kAXTitleAttribute)
        let res = (first, wTitle.isEmpty ? app.name : wTitle)
        state.cachedAppWindows[app.pid] = res
        return res
    }
    return (appEl, app.name)
}

func computeUidDiff(oldNodes: [NodeRecord], newNodes: [NodeRecord]) -> [String] {
    var oldByUid: [String: NodeRecord] = [:]
    for n in oldNodes { oldByUid[n.uid] = n }
    var newByUid: [String: NodeRecord] = [:]
    for n in newNodes { newByUid[n.uid] = n }

    var delta: [String] = []
    for old in oldNodes where newByUid[old.uid] == nil {
        delta.append("- \(old.compactLine)")
    }
    for cur in newNodes {
        if let prev = oldByUid[cur.uid] {
            if prev.bodyWithoutUid != cur.bodyWithoutUid {
                if prev.role == cur.role && prev.label != cur.label && cur.value.isEmpty {
                    delta.append("~ \(cur.uid) \(cur.role) \"\(prev.label)\" -> \"\(cur.label)\"")
                } else if prev.value != cur.value {
                    delta.append("~ \(cur.uid) \(cur.role) \"\(cur.label)\" val=\"\(prev.value)\" -> val=\"\(cur.value)\"")
                } else {
                    delta.append("~ \(cur.compactLine)")
                }
            }
        } else {
            delta.append("+ \(cur.compactLine)")
        }
    }
    return delta
}

let keyMap: [String: CGKeyCode] = [
    "return": 36, "enter": 76, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "clear": 71, "up": 126, "down": 125, "left": 123, "right": 124,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34, "j": 38,
    "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15, "s": 1, "t": 17,
    "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    "+": 24, "=": 24, "-": 27, "*": 67, "/": 44, ".": 47
]

func postKeyChordToPid(_ pid: pid_t, chord: String) {
    let parts = chord.lowercased().split(separator: "+").map { String($0).trimmingCharacters(in: .whitespaces) }
    var flags: CGEventFlags = []
    var mainKey: CGKeyCode?
    for p in parts {
        switch p {
        case "cmd", "command", "super", "meta": flags.insert(.maskCommand)
        case "shift": flags.insert(.maskShift)
        case "alt", "opt", "option": flags.insert(.maskAlternate)
        case "ctrl", "control": flags.insert(.maskControl)
        default: mainKey = keyMap[p]
        }
    }
    guard let code = mainKey,
          let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true),
          let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) else { return }
    down.flags = flags
    up.flags = flags
    down.postToPid(pid)
    up.postToPid(pid)
}

func postBatchedUnicodeTextToPid(_ pid: pid_t, text: String) {
    let utf16 = Array(text.utf16)
    let chunkSize = 16
    var idx = 0
    while idx < utf16.count {
        let end = min(idx + chunkSize, utf16.count)
        var slice = Array(utf16[idx..<end])
        if let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true),
           let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false) {
            down.keyboardSetUnicodeString(stringLength: slice.count, unicodeString: &slice)
            up.keyboardSetUnicodeString(stringLength: slice.count, unicodeString: &slice)
            down.postToPid(pid)
            up.postToPid(pid)
        }
        idx = end
    }
}

func postBackgroundClickToPid(_ pid: pid_t, x: Int, y: Int) -> Bool {
    let pt = CGPoint(x: Double(x), y: Double(y))
    guard let down = CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left),
          let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left) else {
        return false
    }
    down.postToPid(pid)
    up.postToPid(pid)
    return true
}

func performWindowOCR(app: TargetApp, fallbackNodes: [NodeRecord] = []) -> [String] {
    _ = CGMainDisplayID()
    var cgImage: CGImage?
    var winFrame = CGRect.zero
    let sem = DispatchSemaphore(value: 0)
    if #available(macOS 14.0, *) {
        Task {
            defer { sem.signal() }
            guard let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) else { return }
            let win = content.windows.first(where: { $0.windowID == app.windowID && $0.frame.width > 80 })
                ?? content.windows.first(where: { $0.owningApplication?.processID == app.pid && $0.frame.width > 80 })
            guard let targetWin = win else { return }
            winFrame = targetWin.frame
            let filter = SCContentFilter(desktopIndependentWindow: targetWin)
            let cfg = SCStreamConfiguration()
            cfg.width = max(Int(targetWin.frame.width * 2), 300)
            cfg.height = max(Int(targetWin.frame.height * 2), 200)
            cfg.showsCursor = false
            cgImage = try? await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
        }
        _ = sem.wait(timeout: .now() + 1.5)
    }
    var ocrLines: [String] = []
    if let img = cgImage {
        let req = VNRecognizeTextRequest { request, _ in
            guard let results = request.results as? [VNRecognizedTextObservation] else { return }
            for obs in results.prefix(60) {
                if let top = obs.topCandidates(1).first {
                    let text = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !text.isEmpty {
                        let bbox = obs.boundingBox
                        let w = winFrame.width > 0 ? Double(winFrame.width) : Double(img.width)
                        let h = winFrame.height > 0 ? Double(winFrame.height) : Double(img.height)
                        let rx = Int(Double(winFrame.origin.x) + bbox.midX * w)
                        let ry = Int(Double(winFrame.origin.y) + (1.0 - bbox.midY) * h)
                        ocrLines.append("@ocr text \"\(text)\" (\(rx),\(ry))")
                    }
                }
            }
        }
        req.recognitionLevel = .fast
        req.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(cgImage: img, options: [:])
        try? handler.perform([req])
    }
    if ocrLines.isEmpty && !fallbackNodes.isEmpty {
        for n in fallbackNodes.prefix(60) {
            let text = !n.label.isEmpty ? n.label : n.value
            if !text.isEmpty && n.w > 0 && n.h > 0 {
                let cx = n.x + n.w / 2
                let cy = n.y + n.h / 2
                ocrLines.append("@ocr text \"\(text)\" (\(cx),\(cy))")
            }
        }
    }
    return ["[ocr: \(ocrLines.count) text blocks]"] + ocrLines
}

func invokeMenuBar(_ pid: pid_t, path: [String]) -> Bool {
    let appEl = AXUIElementCreateApplication(pid)
    var ref: CFTypeRef?
    guard AXUIElementCopyAttributeValue(appEl, kAXMenuBarAttribute as CFString, &ref) == .success,
          let menuBar = ref else { return false }
    var current: AXUIElement = (menuBar as! AXUIElement)
    for (idx, segment) in path.enumerated() {
        let lower = segment.lowercased()
        let children = axChildren(current).flatMap { el -> [AXUIElement] in
            let r = axString(el, kAXRoleAttribute).lowercased()
            return (r == "axmenu" || r == "menu") ? axChildren(el) : [el]
        }
        guard let match = children.first(where: { axString($0, kAXTitleAttribute).lowercased() == lower })
            ?? children.first(where: { axString($0, kAXTitleAttribute).lowercased().contains(lower) }) else {
            return false
        }
        if idx == path.count - 1 {
            return AXUIElementPerformAction(match, kAXPressAction as CFString) == .success
        }
        let subChildren = axChildren(match)
        current = subChildren.first ?? match
    }
    return false
}

func settleAndWalkAXTree(
    root: AXUIElement,
    winKey: String,
    prevCrc: String,
    query: String?,
    shouldSettle: Bool
) -> ([NodeRecord], [AXUIElement], String) {
    if shouldSettle {
        Thread.sleep(forTimeInterval: 0.008)
    }
    var (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
    var curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
    if shouldSettle && curCrc == prevCrc {
        Thread.sleep(forTimeInterval: 0.012)
        (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
        curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
    }
    return (nodes, els, curCrc)
}

func executeRequest(_ req: BatchRequest) -> String {
    let t0 = DispatchTime.now().uptimeNanoseconds
    let state = EngineState.shared
    state.lock.lock()
    defer { state.lock.unlock() }

    if req.getCursors == true {
        let sorted = state.agentCursors.values.sorted { $0.agent < $1.agent }
        var lines = ["# ax-cursors count=\(sorted.count)"]
        for c in sorted {
            lines.append("@cursor agent=\"\(c.agent)\" surface=app app=\"\(c.app)\" uid=\(c.uid) pos=(\(c.x),\(c.y)) op=\(c.lastOp)")
        }
        return lines.joined(separator: "\n")
    }

    if req.reset == true {
        let explicitAgent = req.agent ?? req.cursor
        state.reset(agent: explicitAgent)
        if let ag = explicitAgent, !ag.isEmpty {
            return "# fmu-ax state reset agent=\(ag)"
        }
        return "# fmu-ax state reset"
    }
    guard let appName = req.app, !appName.isEmpty else {
        return "Error: missing `app` parameter"
    }
    guard let app = findApp(nameOrBundle: appName) else {
        return "Error: app not found (\(appName))"
    }

    let agentId = req.effectiveAgent
    let hasQuery = !(req.query ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    let (root, winTitle) = getTargetRoot(app, windowFilter: req.window)
    let winKey = "\(app.name.lowercased()):\(winTitle)"
    let baselineKey = "\(agentId)|\(winKey)"
    let pidChanged = (state.appPidByKey[winKey] != nil && state.appPidByKey[winKey] != app.pid)
    if let oldPid = state.appPidByKey[winKey], pidChanged {
        state.cachedAppWindows.removeValue(forKey: oldPid)
        let winPrefix = "\(winKey)|"
        for (sKey, uid) in state.structKeyToUid where sKey.hasPrefix(winPrefix) {
            state.uidToElement.removeValue(forKey: uid)
            state.uidToRecord.removeValue(forKey: uid)
        }
        state.latestWindowNodes.removeValue(forKey: winKey)
    }
    state.appPidByKey[winKey] = app.pid
    let hadBaseline = state.windowBaselines[baselineKey] != nil

    var currentNodes: [NodeRecord]
    var currentElements: [AXUIElement]
    if !pidChanged && !hasQuery,
       let cachedNodes = state.windowBaselines[baselineKey] ?? state.latestWindowNodes[winKey],
       !cachedNodes.isEmpty,
       !(req.steps?.isEmpty ?? true) {
        currentNodes = cachedNodes
        currentElements = cachedNodes.compactMap { state.uidToElement[$0.uid] }
        if currentElements.count != currentNodes.count {
            (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
        }
    } else {
        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
    }
    if !hasQuery && !currentNodes.isEmpty {
        state.latestWindowNodes[winKey] = currentNodes
    }

    if !hadBaseline && !hasQuery && !currentNodes.isEmpty {
        state.windowBaselines[baselineKey] = currentNodes
        state.windowCrc[baselineKey] = crc32(currentNodes.map { $0.compactLine }.joined(separator: "\n"))
    }
    let prevNodes = state.windowBaselines[baselineKey] ?? []
    let prevCrc = state.windowCrc[baselineKey] ?? ""

    var stepErrors: [String] = []
    var ocrOutput: [String] = []
    var didMutate = false

    if let steps = req.steps, !steps.isEmpty {
        for (i, step) in steps.enumerated() {
            let op = step.effectiveOp
            if op != "move" && op != "ocr" {
                didMutate = true
            }
            switch op {
            case "wait":
                Thread.sleep(forTimeInterval: Double(step.ms ?? 30) / 1000.0)
            case "settle":
                Thread.sleep(forTimeInterval: 0.010)
            case "ocr":
                ocrOutput.append(contentsOf: performWindowOCR(app: app, fallbackNodes: currentNodes))
            case "key":
                if let chord = step.key ?? step.name ?? step.value {
                    let lc = chord.lowercased()
                    var handledViaAX = false
                    if lc == "escape" || lc == "esc" || lc == "clear" {
                        if let clrIdx = currentNodes.firstIndex(where: {
                            $0.role == "button" && ($0.label.lowercased() == "clear" || $0.label.lowercased() == "all clear")
                        }), clrIdx < currentElements.count {
                            if AXUIElementPerformAction(currentElements[clrIdx], kAXPressAction as CFString) == .success {
                                handledViaAX = true
                            }
                        }
                    }
                    if !handledViaAX {
                        postKeyChordToPid(app.pid, chord: chord)
                        Thread.sleep(forTimeInterval: 0.012)
                    }
                }
            case "type":
                if let text = step.value ?? step.name {
                    postBatchedUnicodeTextToPid(app.pid, text: text)
                }
            case "menu":
                if let p = step.path, !p.isEmpty {
                    if !invokeMenuBar(app.pid, path: p) && !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): menu not found")
                    }
                }
            case "click", "set", "move":
                if let cx = step.x, let cy = step.y, (op == "click" || op == "move") {
                    let hitIdx = currentNodes.firstIndex(where: { n in
                        n.w > 0 && n.h > 0 && cx >= n.x && cx <= (n.x + n.w) && cy >= n.y && cy <= (n.y + n.h)
                    })
                    let hitNode = hitIdx != nil ? currentNodes[hitIdx!] : nil
                    state.agentCursors[agentId] = VirtualCursorState(
                        agent: agentId,
                        app: app.name,
                        window: winTitle,
                        uid: hitNode?.uid ?? "@xy",
                        label: hitNode?.label ?? "",
                        x: cx,
                        y: cy,
                        lastOp: op
                    )
                    if op == "move" {
                        continue
                    }
                    var clickedViaAX = false
                    if let hIdx = hitIdx, hIdx < currentElements.count {
                        let hitEl = currentElements[hIdx]
                        if AXUIElementPerformAction(hitEl, kAXPressAction as CFString) == .success
                            || AXUIElementPerformAction(hitEl, kAXConfirmAction as CFString) == .success {
                            clickedViaAX = true
                        }
                    }
                    if !clickedViaAX {
                        let appEl = AXUIElementCreateApplication(app.pid)
                        var hitRef: AXUIElement?
                        if AXUIElementCopyElementAtPosition(appEl, Float(cx), Float(cy), &hitRef) == .success,
                           let hitEl = hitRef {
                            if AXUIElementPerformAction(hitEl, kAXPressAction as CFString) == .success
                                || AXUIElementPerformAction(hitEl, kAXConfirmAction as CFString) == .success {
                                clickedViaAX = true
                            }
                        }
                    }
                    if !clickedViaAX {
                        _ = postBackgroundClickToPid(app.pid, x: cx, y: cy)
                    }
                    continue
                }
                var targetEl: AXUIElement?
                var targetNode: NodeRecord?
                if let uid = step.effectiveUid {
                    targetEl = state.uidToElement[uid]
                    targetNode = state.uidToRecord[uid]
                    if targetEl == nil {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        targetEl = state.uidToElement[uid]
                        targetNode = state.uidToRecord[uid]
                    }
                } else if let idx = step.index, idx >= 1 && idx <= currentElements.count {
                    targetEl = currentElements[idx - 1]
                    targetNode = currentNodes[idx - 1]
                } else if let rawName = step.effectiveName?.lowercased() {
                    let roleFilter = step.role?.lowercased()
                    let candidates = rawName.split(separator: "|").map { String($0).trimmingCharacters(in: .whitespaces) }
                    for cand in candidates {
                        let exactPos = currentNodes.firstIndex { node in
                            (roleFilter == nil || node.role == roleFilter) && node.label.lowercased() == cand
                        }
                        let subPos = exactPos ?? currentNodes.firstIndex { node in
                            (roleFilter == nil || node.role == roleFilter) && node.label.lowercased().contains(cand)
                        }
                        if let pos = subPos, pos < currentElements.count {
                            targetEl = currentElements[pos]
                            targetNode = currentNodes[pos]
                            break
                        }
                    }
                }

                guard var el = targetEl else {
                    if !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): target not found (\(step.effectiveUid ?? step.effectiveName ?? ""))")
                    }
                    continue
                }

                if let rec = targetNode {
                    let vx = rec.w > 0 ? rec.x + rec.w / 2 : rec.x
                    let vy = rec.h > 0 ? rec.y + rec.h / 2 : rec.y
                    state.agentCursors[agentId] = VirtualCursorState(
                        agent: agentId,
                        app: app.name,
                        window: winTitle,
                        uid: rec.uid,
                        label: rec.label,
                        x: vx,
                        y: vy,
                        lastOp: op
                    )
                }

                if op == "move" {
                    continue
                } else if op == "click" {
                    var err = AXUIElementPerformAction(el, kAXPressAction as CFString)
                    if err == .invalidUIElement || err == .cannotComplete {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        if let uid = step.effectiveUid ?? targetNode?.uid, let refreshed = state.uidToElement[uid] {
                            el = refreshed
                            targetNode = state.uidToRecord[uid]
                            err = AXUIElementPerformAction(el, kAXPressAction as CFString)
                        }
                    }
                    if err != .success {
                        err = AXUIElementPerformAction(el, kAXConfirmAction as CFString)
                    }
                    if err != .success {
                        if let rec = targetNode, rec.w > 0 && rec.h > 0 {
                            if postBackgroundClickToPid(app.pid, x: rec.x + rec.w / 2, y: rec.y + rec.h / 2) {
                                err = .success
                            }
                        }
                    }
                    if err != .success && !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): AXPress failed (\(err.rawValue))")
                    }
                } else {
                    let val = (step.value ?? "") as CFTypeRef
                    _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                    var err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, val)
                    if err == .invalidUIElement || err == .cannotComplete {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        if let uid = step.effectiveUid ?? targetNode?.uid, let refreshed = state.uidToElement[uid] {
                            el = refreshed
                            _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                            err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, val)
                        }
                    }
                    if err != .success {
                        postBatchedUnicodeTextToPid(app.pid, text: step.value ?? "")
                    }
                }
            default:
                break
            }
        }
    }

    let newCrc: String
    if didMutate {
        let settled = settleAndWalkAXTree(
            root: root,
            winKey: winKey,
            prevCrc: prevCrc,
            query: req.query,
            shouldSettle: req.settle ?? true
        )
        currentNodes = settled.0
        currentElements = settled.1
        newCrc = settled.2
        if !hasQuery && !currentNodes.isEmpty {
            state.latestWindowNodes[winKey] = currentNodes
        }
    } else {
        newCrc = crc32(currentNodes.map { $0.compactLine }.joined(separator: "\n"))
    }

    if req.ocr == true || (currentNodes.isEmpty && ocrOutput.isEmpty && !hasQuery) {
        ocrOutput.append(contentsOf: performWindowOCR(app: app, fallbackNodes: currentNodes))
    }

    if !hasQuery && !currentNodes.isEmpty {
        state.windowBaselines[baselineKey] = currentNodes
        state.windowCrc[baselineKey] = newCrc
    }

    let elapsedMs = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000.0
    let hasMoveStep = (req.steps ?? []).contains { $0.effectiveOp == "move" }
    let useDiff = !hasQuery && (req.diff ?? ((didMutate || hasMoveStep) && hadBaseline))

    var out: [String] = []
    let docKey = "\(agentId)|\(app.name)"
    let isFirstDoc = !state.documentedApps.contains(docKey)
    state.documentedApps.insert(docKey)

    var header = "# app=\"\(app.name)\" pid=\(app.pid) win=\"\(winTitle)\""
    if agentId != "default" {
        header += " agent=\"\(agentId)\""
    }
    if (agentId != "default" || hasMoveStep), let cur = state.agentCursors[agentId] {
        header += " cursor=\(cur.uid)@(\(cur.x),\(cur.y))"
    }
    header += " els=\(currentNodes.count) crc=\(newCrc) ms=\(String(format: "%.1f", elapsedMs))"
    if isFirstDoc || (req.showDocs == true) {
        header += "\n# api: click(uid|name), set(uid|name,value), move(uid|name), type(value), key(chord), menu([path]), ocr()"
    }
    out.append(header)

    if !stepErrors.isEmpty {
        out.append("! errors: \(stepErrors.joined(separator: "; "))")
    }
    if !ocrOutput.isEmpty {
        out.append(contentsOf: ocrOutput)
    }

    if useDiff && !prevNodes.isEmpty {
        if prevCrc == newCrc {
            out.append("= unchanged (crc=\(newCrc))")
        } else {
            let delta = computeUidDiff(oldNodes: prevNodes, newNodes: currentNodes)
            if delta.isEmpty {
                out.append("= unchanged (crc=\(newCrc))")
            } else {
                out.append(contentsOf: delta)
            }
        }
    } else {
        for n in currentNodes {
            out.append(n.compactLine)
        }
    }
    return out.joined(separator: "\n")
}

let defaultSocketPath = "/tmp/fmu-ax.sock"

func trySendToDaemon(socketPath: String, payload: String) -> String? {
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { return nil }
    defer { close(fd) }

    var addr = sockaddr_un()
    addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    addr.sun_family = sa_family_t(AF_UNIX)
    let pathBytes = socketPath.utf8CString
    guard pathBytes.count <= MemoryLayout.size(ofValue: addr.sun_path) else { return nil }
    withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
        ptr.withMemoryRebound(to: CChar.self, capacity: pathBytes.count) { dst in
            for i in 0..<pathBytes.count { dst[i] = pathBytes[i] }
        }
    }

    let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
    let connected = withUnsafePointer(to: &addr) { ptr in
        ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            connect(fd, sa, addrLen)
        }
    }
    guard connected == 0 else { return nil }

    var data = Array((payload + "\n").utf8)
    _ = write(fd, &data, data.count)
    shutdown(fd, SHUT_WR)

    var responseData = Data()
    var buf = [UInt8](repeating: 0, count: 8192)
    while true {
        let n = read(fd, &buf, buf.count)
        if n <= 0 { break }
        responseData.append(buf, count: n)
    }
    return String(data: responseData, encoding: .utf8)
}

func runDaemon(socketPath: String) -> Never {
    signal(SIGPIPE, SIG_IGN)
    unlink(socketPath)
    let serverFd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard serverFd >= 0 else {
        fputs("Failed to create socket\n", stderr)
        exit(1)
    }

    var addr = sockaddr_un()
    addr.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    addr.sun_family = sa_family_t(AF_UNIX)
    let pathBytes = socketPath.utf8CString
    withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
        ptr.withMemoryRebound(to: CChar.self, capacity: pathBytes.count) { dst in
            for i in 0..<pathBytes.count { dst[i] = pathBytes[i] }
        }
    }

    let addrLen = socklen_t(MemoryLayout<sockaddr_un>.size)
    let bound = withUnsafePointer(to: &addr) { ptr in
        ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
            bind(serverFd, sa, addrLen)
        }
    }
    guard bound == 0, listen(serverFd, 32) == 0 else {
        fputs("Failed to bind/listen on \(socketPath)\n", stderr)
        exit(1)
    }

    while true {
        let clientFd = accept(serverFd, nil, nil)
        if clientFd < 0 { continue }
        DispatchQueue.global(qos: .userInteractive).async {
            var reqData = Data()
            var buf = [UInt8](repeating: 0, count: 8192)
            while true {
                let n = read(clientFd, &buf, buf.count)
                if n <= 0 { break }
                reqData.append(buf, count: n)
                if reqData.last == UInt8(ascii: "\n") { break }
            }
            let trimmed = String(data: reqData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if trimmed.isEmpty {
                close(clientFd)
                return
            }
            let resp: String
            if let cleanData = trimmed.data(using: .utf8),
               let req = try? JSONDecoder().decode(BatchRequest.self, from: cleanData) {
                resp = executeRequest(req)
            } else {
                resp = "Error: invalid JSON request"
            }
            var outBytes = Array(resp.utf8)
            _ = write(clientFd, &outBytes, outBytes.count)
            close(clientFd)
        }
    }
}

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "--daemon" {
    let sock = args.count >= 3 ? args[2] : defaultSocketPath
    runDaemon(socketPath: sock)
}

let rawPayload: String
if args.count >= 2 {
    if args[1] == "--reset" || args[1] == "reset" {
        rawPayload = "{\"reset\":true}"
    } else {
        rawPayload = args[1]
    }
} else if isatty(STDIN_FILENO) == 0 {
    let stdinData = FileHandle.standardInput.readDataToEndOfFile()
    rawPayload = String(data: stdinData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if rawPayload.isEmpty {
        fputs("Usage: fmu-ax '<json-batch>' | fmu-ax --reset | fmu-ax --daemon [socket-path]\n", stderr)
        exit(2)
    }
} else {
    fputs("Usage: fmu-ax '<json-batch>' | fmu-ax --reset | fmu-ax --daemon [socket-path]\n", stderr)
    exit(2)
}

if let daemonResp = trySendToDaemon(socketPath: defaultSocketPath, payload: rawPayload) {
    print(daemonResp)
    exit(0)
}

guard let reqData = rawPayload.data(using: .utf8),
      let req = try? JSONDecoder().decode(BatchRequest.self, from: reqData) else {
    fputs("Invalid JSON request\n", stderr)
    exit(2)
}

print(executeRequest(req))
