import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation
import ImageIO
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

func parseNormalizedAxUid(_ raw: String?) -> String? {
    guard let c = raw?.trimmingCharacters(in: .whitespacesAndNewlines), c.count >= 3 else {
        return nil
    }
    if c.hasPrefix("@a") || c.hasPrefix("@u") || c.hasPrefix("@e") {
        let suffix = c.dropFirst(2)
        if !suffix.isEmpty && suffix.allSatisfy({ $0.isNumber }) {
            return "@a" + String(suffix)
        }
    }
    return nil
}

private func splitMenuPathString(_ raw: String) -> [String] {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return [] }
    let normalized = trimmed
        .replacingOccurrences(of: "→", with: ">")
        .replacingOccurrences(of: "/", with: ">")
    if normalized.contains(">") {
        return normalized
            .split(separator: ">")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
    return [trimmed]
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
    let label: String?
    let text: String?
    let key: String?
    let value: String?
    let path: [String]?
    let files: [String]?
    let ms: Int?
    let x: Int?
    let y: Int?
    let optional: Bool?

    enum CodingKeys: String, CodingKey {
        case op, action, uid, ref, target, selector, index, role, name, label, text, key, value, path, menu, files, ms, x, y, optional
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func decodeStr(_ k: CodingKeys) -> String? {
            if let s = try? c.decodeIfPresent(String.self, forKey: k) { return s }
            if let i = try? c.decodeIfPresent(Int.self, forKey: k) { return String(i) }
            if let d = try? c.decodeIfPresent(Double.self, forKey: k) { return String(d) }
            if let b = try? c.decodeIfPresent(Bool.self, forKey: k) { return String(b) }
            return nil
        }
        func decodeInt(_ k: CodingKeys) -> Int? {
            if let i = try? c.decodeIfPresent(Int.self, forKey: k) { return i }
            if let d = try? c.decodeIfPresent(Double.self, forKey: k) { return Int(d.rounded()) }
            if let s = try? c.decodeIfPresent(String.self, forKey: k) {
                let clean = s.trimmingCharacters(in: .whitespacesAndNewlines)
                if let i = Int(clean) { return i }
                if let d = Double(clean) { return Int(d.rounded()) }
            }
            return nil
        }
        self.op = decodeStr(.op)
        self.action = decodeStr(.action)
        self.uid = decodeStr(.uid)
        self.ref = decodeStr(.ref)
        self.target = decodeStr(.target)
        self.selector = decodeStr(.selector)
        self.index = decodeInt(.index)
        self.role = decodeStr(.role)
        self.name = decodeStr(.name)
        self.label = decodeStr(.label)
        self.text = decodeStr(.text)
        self.key = decodeStr(.key)
        self.value = decodeStr(.value)
        if let arr = (try? c.decodeIfPresent([String].self, forKey: .path)) ?? (try? c.decodeIfPresent([String].self, forKey: .menu)) {
            self.path = arr
        } else if let str = decodeStr(.path) ?? decodeStr(.menu) {
            let split = splitMenuPathString(str)
            self.path = split.isEmpty ? nil : split
        } else {
            self.path = nil
        }
        if let fArr = try? c.decodeIfPresent([String].self, forKey: .files) {
            self.files = fArr
        } else if let fStr = decodeStr(.files), !fStr.isEmpty {
            self.files = [fStr]
        } else {
            self.files = nil
        }
        self.ms = decodeInt(.ms)
        self.x = decodeInt(.x)
        self.y = decodeInt(.y)
        self.optional = try? c.decodeIfPresent(Bool.self, forKey: .optional)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(op, forKey: .op)
        try c.encodeIfPresent(action, forKey: .action)
        try c.encodeIfPresent(uid, forKey: .uid)
        try c.encodeIfPresent(ref, forKey: .ref)
        try c.encodeIfPresent(target, forKey: .target)
        try c.encodeIfPresent(selector, forKey: .selector)
        try c.encodeIfPresent(index, forKey: .index)
        try c.encodeIfPresent(role, forKey: .role)
        try c.encodeIfPresent(name, forKey: .name)
        try c.encodeIfPresent(label, forKey: .label)
        try c.encodeIfPresent(text, forKey: .text)
        try c.encodeIfPresent(key, forKey: .key)
        try c.encodeIfPresent(value, forKey: .value)
        try c.encodeIfPresent(path, forKey: .path)
        try c.encodeIfPresent(files, forKey: .files)
        try c.encodeIfPresent(ms, forKey: .ms)
        try c.encodeIfPresent(x, forKey: .x)
        try c.encodeIfPresent(y, forKey: .y)
        try c.encodeIfPresent(optional, forKey: .optional)
    }

    var effectiveMenuPath: [String]? {
        if let p = path, !p.isEmpty { return p }
        for cand in [target, name, label, value, text] {
            if let s = cand {
                let split = splitMenuPathString(s)
                if !split.isEmpty { return split }
            }
        }
        return nil
    }

    var effectiveOp: String {
        let raw = (op ?? action ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch raw {
        case "press":
            if key != nil && uid == nil && ref == nil && target == nil && label == nil && index == nil {
                return "key"
            }
            return "click"
        case "type":
            let hasExplicitTarget = (uid != nil || ref != nil || target != nil || selector != nil || label != nil || index != nil)
                || (name != nil && (value != nil || text != nil))
                || (effectiveUid != nil)
            if hasExplicitTarget {
                return "set"
            }
            return "type"
        case "tap":
            return "click"
        case "fill", "input":
            return "set"
        case "hover", "cursor", "point":
            return "move"
        case "sleep", "delay":
            return "wait"
        case "hotkey", "shortcut":
            return "key"
        default:
            return raw
        }
    }

    var effectiveUid: String? {
        for cand in [uid, ref, target, selector, name, label] {
            if let norm = parseNormalizedAxUid(cand) {
                return norm
            }
        }
        return nil
    }

    var effectiveName: String? {
        for cand in [name, label, target, ref, selector] {
            if let c = cand?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty,
               parseNormalizedAxUid(c) == nil {
                return c
            }
        }
        return nil
    }

    var effectiveValue: String? {
        return value ?? text
    }

    var effectiveKey: String? {
        for cand in [key, name, value, text, target] {
            if let c = cand?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty {
                return c
            }
        }
        return nil
    }
}

enum WindowSpec: Codable {
    case titleOrUrl(String)
    case index(Int)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let idx = try? container.decode(Int.self) {
            self = .index(idx)
        } else if let str = try? container.decode(String.self) {
            self = .titleOrUrl(str)
        } else {
            self = .titleOrUrl("")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .titleOrUrl(let s): try container.encode(s)
        case .index(let i): try container.encode(i)
        }
    }
}

struct PreviewNodeSpec: Codable {
    let uid: String?
    let role: String?
    let label: String?
    let value: String?
    let x: Int?
    let y: Int?
    let w: Int?
    let h: Int?
}

struct StatusUpdateSpec: Codable {
    let state: String?
    let surface: String?
    let target: String?
    let op: String?
    let agent: String?
    let stepCount: Int?
    let cursorX: Int?
    let cursorY: Int?
    let cursorLabel: String?
    let winW: Int?
    let winH: Int?
    let previewPath: String?
    let previewNodes: [PreviewNodeSpec]?
    let isHeadless: Bool?
}

struct BatchRequest: Codable {
    let agent: String?
    let cursor: String?
    let app: String?
    let window: WindowSpec?
    let windowIndex: Int?
    let url: String?
    let windowUrl: String?
    let query: String?
    let steps: [StepSpec]?
    let diff: Bool?
    let settle: Bool?
    let reset: Bool?
    let showDocs: Bool?
    let ocr: Bool?
    let getCursors: Bool?
    let stop: Bool?
    let resume: Bool?
    let statusBar: Bool?
    let clickStatusBar: Bool?
    let highlightWindow: Bool?
    let togglePreview: Bool?
    let toggleZoom: Bool?
    let dumpUiSnapshots: String?
    let statusUpdate: StatusUpdateSpec?
    let imagePath: String?
    let clipX: Double?
    let clipY: Double?
    let clipW: Double?
    let clipH: Double?

    var effectiveAgent: String {
        let raw = (agent ?? cursor ?? "default").trimmingCharacters(in: .whitespacesAndNewlines)
        return raw.isEmpty ? "default" : raw
    }

    var effectiveWindowIndex: Int? {
        if let idx = windowIndex { return idx }
        if case .index(let idx) = window { return idx }
        if case .titleOrUrl(let raw) = window {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if let direct = Int(trimmed) { return direct }
            if trimmed.hasPrefix("#"), let n = Int(trimmed.dropFirst()) { return n }
            if trimmed.hasPrefix("index:"), let n = Int(trimmed.dropFirst(6).trimmingCharacters(in: .whitespaces)) { return n }
        }
        return nil
    }

    var effectiveWindowFilter: String? {
        if effectiveWindowIndex != nil { return nil }
        if case .titleOrUrl(let raw) = window {
            let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        return nil
    }

    var effectiveUrlFilter: String? {
        for cand in [windowUrl, url] {
            if let c = cand?.trimmingCharacters(in: .whitespacesAndNewlines), !c.isEmpty {
                return c
            }
        }
        return nil
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
    var latestWindowElements: [String: [AXUIElement]] = [:]
    var windowSyncedSeq: [String: UInt64] = [:]
    var cachedAppWindows: [String: (AXUIElement, String)] = [:]
    var appCache: [String: TargetApp] = [:]
    var appPidByKey: [String: pid_t] = [:]
    var wokenPids: Set<pid_t> = []
    var documentedApps: Set<String> = []
    var agentCursors: [String: VirtualCursorState] = [:]

    func invalidateAppWindows(forPid pid: pid_t) {
        let prefix = "\(pid)|"
        cachedAppWindows = cachedAppWindows.filter { !$0.key.hasPrefix(prefix) }
    }

    // Push-based AXObserver state per PID
    let observerLock = NSLock()
    var observersByPid: [pid_t: AXObserver] = [:]
    var pidMutationSeq: [pid_t: UInt64] = [:]
    var dirtyElementHashes: [pid_t: Set<UInt>] = [:]
    var structuralDirtyPids: Set<pid_t> = []
    var observerRunLoop: CFRunLoop?
    var observerThreadStarted: Bool = false

    func currentPidSeq(_ pid: pid_t) -> UInt64 {
        observerLock.lock()
        defer { observerLock.unlock() }
        return pidMutationSeq[pid, default: 0]
    }

    func isStructurallyDirty(_ pid: pid_t) -> Bool {
        observerLock.lock()
        defer { observerLock.unlock() }
        return structuralDirtyPids.contains(pid)
    }

    func consumeDirtyState(_ pid: pid_t) -> (seq: UInt64, structural: Bool, hashes: Set<UInt>) {
        observerLock.lock()
        defer { observerLock.unlock() }
        let seq = pidMutationSeq[pid, default: 0]
        let structural = structuralDirtyPids.remove(pid) != nil
        let hashes = dirtyElementHashes.removeValue(forKey: pid) ?? []
        return (seq, structural, hashes)
    }

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
        latestWindowElements.removeAll()
        windowSyncedSeq.removeAll()
        cachedAppWindows.removeAll()
        appCache.removeAll()
        appPidByKey.removeAll()
        wokenPids.removeAll()
        documentedApps.removeAll()
        agentCursors.removeAll()
        observerLock.lock()
        dirtyElementHashes.removeAll()
        structuralDirtyPids.removeAll()
        observerLock.unlock()
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
    if let u = val as? URL {
        return u.absoluteString.trimmingCharacters(in: .whitespacesAndNewlines)
    }
    if let u = val as? NSURL, let s = u.absoluteString {
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
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

let axObserverCallback: AXObserverCallback = { _, element, notification, refcon in
    guard let refcon = refcon else { return }
    let pid = pid_t(Int(bitPattern: refcon))
    let notif = notification as String
    let elemHash = CFHash(element)
    let state = EngineState.shared
    state.observerLock.lock()
    state.pidMutationSeq[pid, default: 0] &+= 1
    state.dirtyElementHashes[pid, default: []].insert(elemHash)
    if notif == (kAXUIElementDestroyedNotification as String)
        || notif == (kAXCreatedNotification as String)
        || notif == (kAXWindowCreatedNotification as String)
        || notif == (kAXLayoutChangedNotification as String)
        || notif == (kAXFocusedWindowChangedNotification as String) {
        state.structuralDirtyPids.insert(pid)
    }
    state.observerLock.unlock()
}

func ensureAXObserverRunLoop() -> CFRunLoop? {
    let state = EngineState.shared
    state.observerLock.lock()
    if let rl = state.observerRunLoop {
        state.observerLock.unlock()
        return rl
    }
    if !state.observerThreadStarted {
        state.observerThreadStarted = true
        let sem = DispatchSemaphore(value: 0)
        let t = Thread {
            let rl = CFRunLoopGetCurrent()
            state.observerLock.lock()
            state.observerRunLoop = rl
            state.observerLock.unlock()
            sem.signal()
            var ctx = CFRunLoopSourceContext()
            if let dummySrc = CFRunLoopSourceCreate(kCFAllocatorDefault, 0, &ctx) {
                CFRunLoopAddSource(rl, dummySrc, .defaultMode)
            }
            CFRunLoopRun()
        }
        t.name = "fmu-ax-observer"
        t.qualityOfService = .userInteractive
        t.start()
        state.observerLock.unlock()
        _ = sem.wait(timeout: .now() + 0.5)
        state.observerLock.lock()
    }
    let rl = state.observerRunLoop
    state.observerLock.unlock()
    return rl
}

func ensureAXObserver(_ pid: pid_t, appElement: AXUIElement) {
    let state = EngineState.shared
    state.observerLock.lock()
    if state.observersByPid[pid] != nil {
        state.observerLock.unlock()
        return
    }
    state.observerLock.unlock()

    guard let rl = ensureAXObserverRunLoop() else { return }
    var obsRef: AXObserver?
    guard AXObserverCreate(pid, axObserverCallback, &obsRef) == .success,
          let obs = obsRef else { return }

    let refcon = UnsafeMutableRawPointer(bitPattern: Int(pid))
    let notifications: [String] = [
        kAXValueChangedNotification as String,
        kAXTitleChangedNotification as String,
        kAXUIElementDestroyedNotification as String,
        kAXCreatedNotification as String,
        kAXWindowCreatedNotification as String,
        kAXFocusedWindowChangedNotification as String,
        kAXFocusedUIElementChangedNotification as String,
        kAXSelectedChildrenChangedNotification as String,
        kAXLayoutChangedNotification as String
    ]
    for notif in notifications {
        _ = AXObserverAddNotification(obs, appElement, notif as CFString, refcon)
    }
    CFRunLoopAddSource(rl, AXObserverGetRunLoopSource(obs), .defaultMode)
    CFRunLoopWakeUp(rl)

    state.observerLock.lock()
    state.observersByPid[pid] = obs
    state.observerLock.unlock()
}

func wakeLazyAXIfNeeded(_ pid: pid_t, appElement: AXUIElement) {
    let state = EngineState.shared
    if !state.wokenPids.contains(pid) {
        state.wokenPids.insert(pid)
        _ = AXUIElementSetAttributeValue(appElement, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        _ = AXUIElementSetAttributeValue(appElement, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    }
    ensureAXObserver(pid, appElement: appElement)
}

func collectDescendantStaticText(
    _ el: AXUIElement,
    initialChildren: [AXUIElement]? = nil,
    maxNodes: Int = 96
) -> (text: String, staticCount: Int, childCount: Int) {
    let topChildren = initialChildren ?? axChildren(el)
    if topChildren.isEmpty {
        return ("", 0, 0)
    }
    var visited = 0
    var blockParts: [String] = []
    var staticCount = 0
    for top in topChildren {
        if visited >= maxNodes { break }
        visited += 1
        let rawRole = axString(top, kAXRoleAttribute)
        let r = rawRole.hasPrefix("AX") ? String(rawRole.dropFirst(2)).lowercased() : rawRole.lowercased()
        if r == "statictext" {
            staticCount += 1
            let v = axString(top, kAXValueAttribute)
            let t = !v.isEmpty ? v : axString(top, kAXTitleAttribute)
            if !t.isEmpty {
                blockParts.append(t)
            }
        } else {
            var inlineRuns: [String] = []
            var stack: [AXUIElement] = axChildren(top)
            while !stack.isEmpty && visited < maxNodes {
                let cur = stack.removeFirst()
                visited += 1
                let crRaw = axString(cur, kAXRoleAttribute)
                let cr = crRaw.hasPrefix("AX") ? String(crRaw.dropFirst(2)).lowercased() : crRaw.lowercased()
                if cr == "statictext" {
                    staticCount += 1
                    let v = axString(cur, kAXValueAttribute)
                    let t = !v.isEmpty ? v : axString(cur, kAXTitleAttribute)
                    if !t.isEmpty {
                        inlineRuns.append(t)
                    }
                } else {
                    let ch = axChildren(cur)
                    if !ch.isEmpty {
                        stack.insert(contentsOf: ch, at: 0)
                    }
                }
            }
            let joined = inlineRuns.joined(separator: "").trimmingCharacters(in: .whitespaces)
            if !joined.isEmpty {
                blockParts.append(joined)
            }
        }
    }
    return (blockParts.joined(separator: " "), staticCount, topChildren.count)
}

func isElementInWebArea(_ el: AXUIElement) -> Bool {
    var cur: AXUIElement? = el
    for _ in 0..<64 {
        guard let c = cur else { break }
        let role = axString(c, kAXRoleAttribute)
        if role == "AXWebArea" {
            return true
        }
        if role == "AXWindow" || role == "AXApplication" || role.isEmpty {
            break
        }
        var parentRef: CFTypeRef?
        if AXUIElementCopyAttributeValue(c, kAXParentAttribute as CFString, &parentRef) == .success,
           let p = parentRef {
            cur = (p as! AXUIElement)
        } else {
            break
        }
    }
    return false
}

func selectAllViaAX(_ el: AXUIElement) -> Bool {
    var charCount = 65535
    var numRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(el, kAXNumberOfCharactersAttribute as CFString, &numRef) == .success,
       let n = numRef as? NSNumber {
        charCount = max(n.intValue, 0)
    }
    var range = CFRange(location: 0, length: max(charCount, 65535))
    guard let rangeVal = AXValueCreate(.cfRange, &range) else { return false }
    return AXUIElementSetAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, rangeVal) == .success
}

func replaceViaAXSelectedText(_ el: AXUIElement, text: String) -> Bool {
    _ = selectAllViaAX(el)
    return AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute as CFString, text as CFTypeRef) == .success
}

func verifyElementSetValue(
    _ el: AXUIElement,
    role: String,
    inWebArea: Bool,
    isSecure: Bool,
    expectedClean: String,
    usedPureAXOnlyOnWebRichText: Bool = false
) -> (verified: Bool, actual: String) {
    let rawVal = axString(el, kAXValueAttribute)
    if isSecure {
        return (true, rawVal)
    }
    if role == "textarea" {
        let desc = collectDescendantStaticText(el)
        let effective = (!desc.text.isEmpty && (desc.staticCount > 1 || desc.text.count > rawVal.count))
            ? desc.text
            : rawVal
        if inWebArea && desc.childCount > 0 {
            if usedPureAXOnlyOnWebRichText {
                return (false, effective)
            }
            if expectedClean.isEmpty {
                return (rawVal.isEmpty && desc.text.isEmpty, effective)
            }
            let ok = (!desc.text.isEmpty && desc.text == expectedClean)
                || (desc.staticCount == 0 && rawVal == expectedClean)
            return (ok, effective)
        }
        return (effective == expectedClean, effective)
    }
    return (rawVal == expectedClean, rawVal)
}

func detectDisabledNSOpenPanel(nodes: [NodeRecord]) -> NodeRecord? {
    let hasSheetOrDialog = nodes.contains { n in
        (n.role == "sheet" || n.role == "dialog")
    }
    guard hasSheetOrDialog else { return nil }
    let hasCancel = nodes.contains { n in
        n.role == "button" && ["cancel", "取消"].contains(n.label.lowercased())
    }
    guard hasCancel else { return nil }
    let openLabels: Set<String> = ["open", "打开", "打開", "choose", "选择", "upload", "上传"]
    return nodes.first { n in
        n.role == "button" && !n.enabled && openLabels.contains(n.label.lowercased())
    }
}

func walkAXTree(_ root: AXUIElement, winKey: String, query: String? = nil, maxElements: Int = 300) -> ([NodeRecord], [AXUIElement]) {
    var records: [NodeRecord] = []
    var elements: [AXUIElement] = []
    var stack: [AXUIElement] = [root]
    var visited = 0
    var visitedHashes: Set<UInt> = []
    var structCounts: [String: Int] = [:]
    var usedInPass: Set<String> = []
    let q = query?.lowercased()
    let state = EngineState.shared

    while !stack.isEmpty && records.count < maxElements && visited < 2800 {
        let el = stack.removeFirst()
        let elHash = CFHash(el)
        if !visitedHashes.insert(elHash).inserted {
            continue
        }
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
        if label.isEmpty && (role == "sheet" || role == "dialog") {
            let sub = axString(el, kAXSubroleAttribute)
            label = !sub.isEmpty ? sub : role
        }
        let children = axChildren(el)
        var val = attrs.value
        if role == "textarea" && !children.isEmpty {
            let desc = collectDescendantStaticText(el, initialChildren: children)
            if !desc.text.isEmpty && (desc.staticCount > 1 || desc.text.count > val.count) {
                val = desc.text
            }
        }

        if interactiveRoles.contains(role) && (!label.isEmpty || !val.isEmpty || role == "textfield" || role == "searchfield" || role == "textarea" || role == "sheet" || role == "dialog") {
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
        if !children.isEmpty {
            if visited == 1 && children.count > 1 {
                var sheets: [AXUIElement] = []
                var others: [AXUIElement] = []
                for ch in children {
                    let cr = axString(ch, kAXRoleAttribute)
                    if cr == "AXSheet" || cr == "AXDialog" {
                        sheets.append(ch)
                    } else {
                        others.append(ch)
                    }
                }
                stack.insert(contentsOf: sheets + others, at: 0)
            } else {
                stack.insert(contentsOf: children, at: 0)
            }
        }
    }
    return (records, elements)
}

func refreshDirtyLeavesInPlace(
    nodes: [NodeRecord],
    elements: [AXUIElement],
    dirtyHashes: Set<UInt>,
    refreshValueBearers: Bool
) -> [NodeRecord]? {
    guard !nodes.isEmpty, nodes.count == elements.count else { return nil }
    let state = EngineState.shared
    var updated = nodes
    for i in 0..<nodes.count {
        let rec = nodes[i]
        let el = elements[i]
        let h = CFHash(el)
        let shouldRefresh = dirtyHashes.contains(h)
            || (refreshValueBearers && (
                rec.role != "button"
                || rec.label.lowercased() == "clear"
                || rec.label.lowercased() == "all clear"
            ))
        if !shouldRefresh { continue }
        let attrs = readBatchedAttrs(el)
        if attrs.role.isEmpty {
            return nil
        }
        var label = !attrs.title.isEmpty ? attrs.title : attrs.desc
        if label.isEmpty && (attrs.role == "textfield" || attrs.role == "textarea" || attrs.role == "searchfield") {
            label = axString(el, "AXPlaceholderValue")
        }
        if label.isEmpty { label = rec.label }
        var val = attrs.value
        if attrs.role == "textarea" {
            let desc = collectDescendantStaticText(el)
            if !desc.text.isEmpty && (desc.staticCount > 1 || desc.text.count > val.count) {
                val = desc.text
            }
        }
        let newRec = NodeRecord(
            uid: rec.uid,
            structKey: rec.structKey,
            role: attrs.role,
            label: label,
            value: val,
            enabled: attrs.enabled,
            x: attrs.x,
            y: attrs.y,
            w: attrs.w,
            h: attrs.h
        )
        updated[i] = newRec
        state.uidToRecord[rec.uid] = newRec
    }
    return updated
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
        if isProcessAndAXAlive(cached.pid) {
            if let cachedWin = state.cachedAppWindows["\(cached.pid)|"],
               axString(cachedWin.0, kAXRoleAttribute) == "AXWindow" {
                return cached
            }
            if hasReadyAXWindow(cached.pid) {
                return cached
            }
        }
        state.appCache.removeValue(forKey: lower)
        state.invalidateAppWindows(forPid: cached.pid)
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

func isWindowMain(_ pid: pid_t, _ win: AXUIElement) -> Bool {
    if axString(win, kAXRoleAttribute) != "AXWindow" {
        return true
    }
    var ref: CFTypeRef?
    if AXUIElementCopyAttributeValue(win, kAXMainAttribute as CFString, &ref) == .success,
       let b = ref as? Bool {
        if b { return true }
    }
    let appEl = AXUIElementCreateApplication(pid)
    var mainRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(appEl, kAXMainWindowAttribute as CFString, &mainRef) == .success,
       let m = mainRef {
        return CFHash(m as! AXUIElement) == CFHash(win)
    }
    var winsRef: CFTypeRef?
    if AXUIElementCopyAttributeValue(appEl, kAXWindowsAttribute as CFString, &winsRef) == .success,
       let wArr = winsRef as? [AXUIElement] {
        return wArr.count <= 1
    }
    return true
}

func extractWindowWebMetadata(_ win: AXUIElement, maxNodes: Int = 120) -> String {
    var parts: [String] = []
    let doc = axString(win, kAXDocumentAttribute)
    if !doc.isEmpty { parts.append(doc) }
    let wUrl = axString(win, kAXURLAttribute)
    if !wUrl.isEmpty { parts.append(wUrl) }

    var queue: [AXUIElement] = axChildren(win)
    var visited = 0
    while !queue.isEmpty && visited < maxNodes {
        let cur = queue.removeFirst()
        visited += 1
        let rawRole = axString(cur, kAXRoleAttribute)
        let role = rawRole.hasPrefix("AX") ? String(rawRole.dropFirst(2)).lowercased() : rawRole.lowercased()
        let curUrl = axString(cur, kAXURLAttribute)
        if !curUrl.isEmpty {
            parts.append(curUrl)
        }
        if role == "webarea" {
            let t = axString(cur, kAXTitleAttribute)
            if !t.isEmpty { parts.append(t) }
        } else if role == "textfield" || role == "searchfield" || role == "combobox" {
            let v = axString(cur, kAXValueAttribute)
            if !v.isEmpty { parts.append(v) }
        }
        if visited < maxNodes {
            let ch = axChildren(cur)
            if !ch.isEmpty {
                queue.append(contentsOf: ch)
            }
        }
    }
    return parts.joined(separator: " ")
}

func collectAppWindows(_ pid: pid_t, includeOffSpace: Bool) -> [AXUIElement] {
    let appEl = AXUIElementCreateApplication(pid)
    wakeLazyAXIfNeeded(pid, appElement: appEl)
    var seen: Set<UInt> = []
    var wins: [AXUIElement] = []
    var fallbackUnknownWins: [AXUIElement] = []
    func addWin(_ w: AXUIElement, fromOffSpaceProbe: Bool = false) {
        guard axString(w, kAXRoleAttribute) == "AXWindow" else { return }
        let title = axString(w, kAXTitleAttribute)
        let subrole = axString(w, kAXSubroleAttribute)
        if fromOffSpaceProbe {
            if title.isEmpty && axChildren(w).isEmpty {
                return
            }
            if title.isEmpty && (subrole == "AXUnknown" || subrole.isEmpty) {
                let h = CFHash(w)
                if !seen.contains(h) {
                    fallbackUnknownWins.append(w)
                }
                return
            }
        }
        let h = CFHash(w)
        if !seen.contains(h) {
            seen.insert(h)
            wins.append(w)
        }
    }
    var ref: CFTypeRef?
    if AXUIElementCopyAttributeValue(appEl, kAXWindowsAttribute as CFString, &ref) == .success,
       let wArr = ref as? [AXUIElement] {
        for w in wArr { addWin(w) }
    }
    if AXUIElementCopyAttributeValue(appEl, kAXMainWindowAttribute as CFString, &ref) == .success,
       let mainWin = ref {
        addWin(mainWin as! AXUIElement)
    }
    if AXUIElementCopyAttributeValue(appEl, kAXFocusedWindowAttribute as CFString, &ref) == .success,
       let focWin = ref {
        addWin(focWin as! AXUIElement)
    }
    if includeOffSpace || wins.isEmpty || !wins.contains(where: { !axChildren($0).isEmpty }) {
        for w in offSpaceWindows(pid) {
            addWin(w, fromOffSpaceProbe: true)
        }
    }
    if wins.isEmpty && !fallbackUnknownWins.isEmpty {
        for w in fallbackUnknownWins {
            let h = CFHash(w)
            if !seen.contains(h) {
                seen.insert(h)
                wins.append(w)
            }
        }
    }
    return wins
}

func describeAvailableWindows(_ pid: pid_t, _ wins: [AXUIElement]) -> String {
    if wins.isEmpty { return "none" }
    return wins.enumerated().map { (idx, w) in
        let title = axString(w, kAXTitleAttribute)
        let mainTag = isWindowMain(pid, w) ? " [main]" : ""
        return "[\(idx + 1)] \"\(title)\"\(mainTag)"
    }.joined(separator: ", ")
}

func extractUrlHost(_ raw: String) -> String? {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    guard !trimmed.isEmpty else { return nil }
    if let u = URL(string: trimmed), let h = u.host, !h.isEmpty {
        return h.hasPrefix("www.") ? String(h.dropFirst(4)) : h
    }
    if !trimmed.contains("://") && (trimmed.contains("/") || trimmed.contains(".")),
       let u = URL(string: "https://" + trimmed),
       let h = u.host, !h.isEmpty, h.contains(".") {
        return h.hasPrefix("www.") ? String(h.dropFirst(4)) : h
    }
    return nil
}

enum WindowResolution {
    case matched(AXUIElement, String)
    case notFound(String)
}

func resolveTargetWindow(
    _ app: TargetApp,
    windowFilter: String? = nil,
    windowIndex: Int? = nil,
    urlFilter: String? = nil
) -> WindowResolution {
    let state = EngineState.shared
    let wf = windowFilter?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let uf = urlFilter?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    let wfLower = wf.lowercased()
    let ufLower = uf.lowercased()
    let hasStringFilter = !wfLower.isEmpty || !ufLower.isEmpty
    let hasFilter = (windowIndex != nil) || hasStringFilter
    let cacheKey: String
    if let idx = windowIndex {
        cacheKey = "\(app.pid)|idx:\(idx)"
    } else if hasStringFilter {
        cacheKey = "\(app.pid)|win:\(wfLower)|url:\(ufLower)"
    } else {
        cacheKey = "\(app.pid)|"
    }

    func windowMatches(_ win: AXUIElement, allowHostFallback: Bool) -> Bool {
        let titleLower = axString(win, kAXTitleAttribute).lowercased()
        var metaLower: String?
        func getMeta() -> String {
            if let m = metaLower { return m }
            let computed = extractWindowWebMetadata(win).lowercased()
            metaLower = computed
            return computed
        }
        if !wfLower.isEmpty {
            var wfOk = titleLower.contains(wfLower) || getMeta().contains(wfLower)
            if !wfOk && allowHostFallback, let host = extractUrlHost(wfLower) {
                wfOk = titleLower.contains(host) || getMeta().contains(host)
            }
            if !wfOk { return false }
        }
        if !ufLower.isEmpty {
            var ufOk = getMeta().contains(ufLower) || titleLower.contains(ufLower)
            if !ufOk && allowHostFallback, let host = extractUrlHost(ufLower) {
                ufOk = getMeta().contains(host) || titleLower.contains(host)
            }
            if !ufOk { return false }
        }
        return true
    }

    if let cached = state.cachedAppWindows[cacheKey] {
        if axString(cached.0, kAXRoleAttribute) == "AXWindow" {
            let liveTitle = axString(cached.0, kAXTitleAttribute)
            let filterStillMatches = !hasStringFilter
                || windowMatches(cached.0, allowHostFallback: true)
            if filterStillMatches && (!state.isStructurallyDirty(app.pid) || !axChildren(cached.0).isEmpty) {
                let effectiveTitle = liveTitle.isEmpty ? cached.1 : liveTitle
                return .matched(cached.0, effectiveTitle)
            }
        }
        state.cachedAppWindows.removeValue(forKey: cacheKey)
    }

    let appEl = AXUIElementCreateApplication(app.pid)
    wakeLazyAXIfNeeded(app.pid, appElement: appEl)
    var wins = collectAppWindows(app.pid, includeOffSpace: false)

    if let idx = windowIndex {
        if idx < 1 || idx > wins.count {
            wins = collectAppWindows(app.pid, includeOffSpace: true)
        }
        guard idx >= 1 && idx <= wins.count else {
            let avail = describeAvailableWindows(app.pid, wins)
            return .notFound("Error: window index \(idx) out of range for \(app.name) (available windows: \(avail))")
        }
        let chosen = wins[idx - 1]
        let wTitle = axString(chosen, kAXTitleAttribute)
        let res = (chosen, wTitle.isEmpty ? app.name : wTitle)
        state.cachedAppWindows[cacheKey] = res
        return .matched(res.0, res.1)
    }

    if hasStringFilter {
        func findMatch(in list: [AXUIElement], allowHostFallback: Bool) -> AXUIElement? {
            if let nonEmpty = list.first(where: {
                windowMatches($0, allowHostFallback: allowHostFallback) && !axChildren($0).isEmpty
            }) {
                return nonEmpty
            }
            return list.first(where: {
                windowMatches($0, allowHostFallback: allowHostFallback)
            })
        }

        var matched = findMatch(in: wins, allowHostFallback: false)
        if matched == nil {
            wins = collectAppWindows(app.pid, includeOffSpace: true)
            matched = findMatch(in: wins, allowHostFallback: false)
        }
        if matched == nil && (extractUrlHost(wfLower) != nil || extractUrlHost(ufLower) != nil) {
            matched = findMatch(in: wins, allowHostFallback: true)
        }
        guard let win = matched else {
            let avail = describeAvailableWindows(app.pid, wins)
            let descFilter = (!wf.isEmpty && !uf.isEmpty)
                ? "window=\"\(wf)\" url=\"\(uf)\""
                : "\"\(!wf.isEmpty ? wf : uf)\""
            return .notFound("Error: window matching \(descFilter) not found in \(app.name) (available windows: \(avail))")
        }
        let wTitle = axString(win, kAXTitleAttribute)
        let res = (win, wTitle.isEmpty ? app.name : wTitle)
        state.cachedAppWindows[cacheKey] = res
        return .matched(res.0, res.1)
    }

    if !wins.isEmpty {
        let first = wins.first(where: { isWindowMain(app.pid, $0) && !axChildren($0).isEmpty })
            ?? wins.first(where: { !axChildren($0).isEmpty })
            ?? wins[0]
        let wTitle = axString(first, kAXTitleAttribute)
        let res = (first, wTitle.isEmpty ? app.name : wTitle)
        if !hasFilter {
            state.cachedAppWindows[cacheKey] = res
        }
        return .matched(res.0, res.1)
    }
    return .matched(appEl, app.name)
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

func performImageFileOCR(
    imagePath: String,
    clipX: Double = 0.0,
    clipY: Double = 0.0,
    clipW: Double = 0.0,
    clipH: Double = 0.0
) -> String {
    let t0 = DispatchTime.now().uptimeNanoseconds
    let url = URL(fileURLWithPath: imagePath)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        return "Error: unable to load image at \(imagePath)"
    }
    var ocrItems: [(text: String, x: Int, y: Int, w: Int, h: Int)] = []
    func runPass(level: VNRequestTextRecognitionLevel) {
        ocrItems.removeAll()
        let req = VNRecognizeTextRequest { request, _ in
            guard let results = request.results as? [VNRecognizedTextObservation] else { return }
            for obs in results.prefix(80) {
                if let top = obs.topCandidates(1).first {
                    let text = top.string.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !text.isEmpty {
                        let bbox = obs.boundingBox
                        let w = clipW > 0 ? clipW : Double(cgImg.width)
                        let h = clipH > 0 ? clipH : Double(cgImg.height)
                        let rx = Int(round(clipX + Double(bbox.midX) * w))
                        let ry = Int(round(clipY + (1.0 - Double(bbox.midY)) * h))
                        let rw = max(1, Int(round(Double(bbox.width) * w)))
                        let rh = max(1, Int(round(Double(bbox.height) * h)))
                        ocrItems.append((text: text, x: rx, y: ry, w: rw, h: rh))
                    }
                }
            }
        }
        req.recognitionLevel = level
        req.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
        try? handler.perform([req])
    }
    runPass(level: .fast)
    if ocrItems.isEmpty {
        runPass(level: .accurate)
    }
    let elapsedMs = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000.0
    var lines = ["# image-ocr count=\(ocrItems.count) ms=\(String(format: "%.1f", elapsedMs))"]
    for (idx, item) in ocrItems.enumerated() {
        lines.append("@ocr\(idx + 1) canvas-text \"\(item.text)\" (\(item.x),\(item.y)) [\(item.w)x\(item.h)]")
    }
    return lines.joined(separator: "\n")
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

let userStoppedErrorMessage = "Error: [USER_STOPPED] User clicked Stop in the macOS menu bar. Do NOT retry or call fmu again; stop immediately and report to the user. (Click '🛑 fmu Stopped ▶ Resume' in the macOS menu bar or run 'fmu --resume' to re-enable.)"

final class FMUHudPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

func cleanDisplayText(_ raw: String) -> String {
    let invisible: Set<Unicode.Scalar> = [
        "\u{200E}", "\u{200F}", "\u{202A}", "\u{202B}", "\u{202C}", "\u{202D}", "\u{202E}",
        "\u{2066}", "\u{2067}", "\u{2068}", "\u{2069}", "\u{FEFF}"
    ]
    var scalars = String.UnicodeScalarView()
    for sc in raw.unicodeScalars where !invisible.contains(sc) {
        scalars.append(sc)
    }
    return String(scalars).trimmingCharacters(in: .whitespacesAndNewlines)
}

final class FMUHighlightView: NSView {
    var targetLabel: String = ""
    var opLabel: String = ""
    var cursorLocalPt: CGPoint?
    var cursorLabel: String = ""
    var pulsePhase: Int = 0

    override var isFlipped: Bool { true }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.saveGState()
        defer { ctx.restoreGState() }

        let pulseAlpha: CGFloat = (pulsePhase % 2 == 0) ? 0.96 : 0.74
        let outerInset = bounds.insetBy(dx: 2.0, dy: 2.0)
        let glowRect = bounds.insetBy(dx: 4.5, dy: 4.5)

        // Subtle outer emerald glow
        let glowPath = NSBezierPath(roundedRect: outerInset, xRadius: 12, yRadius: 12)
        glowPath.lineWidth = 6.0
        NSColor(calibratedRed: 0.12, green: 0.88, blue: 0.40, alpha: 0.30 * pulseAlpha).setStroke()
        glowPath.stroke()

        // Crisp neon green border around the target window
        let borderPath = NSBezierPath(roundedRect: glowRect, xRadius: 10, yRadius: 10)
        borderPath.lineWidth = 2.8
        NSColor(calibratedRed: 0.20, green: 0.95, blue: 0.45, alpha: pulseAlpha).setStroke()
        borderPath.stroke()

        // High-contrast viewfinder corner brackets
        let bLen: CGFloat = min(22.0, min(glowRect.width, glowRect.height) * 0.16)
        let corners = NSBezierPath()
        // Top-left
        corners.move(to: NSPoint(x: glowRect.minX, y: glowRect.minY + bLen))
        corners.line(to: NSPoint(x: glowRect.minX, y: glowRect.minY))
        corners.line(to: NSPoint(x: glowRect.minX + bLen, y: glowRect.minY))
        // Top-right
        corners.move(to: NSPoint(x: glowRect.maxX - bLen, y: glowRect.minY))
        corners.line(to: NSPoint(x: glowRect.maxX, y: glowRect.minY))
        corners.line(to: NSPoint(x: glowRect.maxX, y: glowRect.minY + bLen))
        // Bottom-left
        corners.move(to: NSPoint(x: glowRect.minX, y: glowRect.maxY - bLen))
        corners.line(to: NSPoint(x: glowRect.minX, y: glowRect.maxY))
        corners.line(to: NSPoint(x: glowRect.minX + bLen, y: glowRect.maxY))
        // Bottom-right
        corners.move(to: NSPoint(x: glowRect.maxX - bLen, y: glowRect.maxY))
        corners.line(to: NSPoint(x: glowRect.maxX, y: glowRect.maxY))
        corners.line(to: NSPoint(x: glowRect.maxX, y: glowRect.maxY - bLen))
        corners.lineWidth = 4.2
        corners.lineCapStyle = .round
        corners.lineJoinStyle = .round
        NSColor(calibratedRed: 0.40, green: 1.0, blue: 0.62, alpha: 1.0).setStroke()
        corners.stroke()

        // Top floating banner pill showing which window fmu is controlling (auto-truncating on narrow windows)
        let cleanTarget = cleanDisplayText(targetLabel.isEmpty ? "Target Window" : targetLabel)
        let cleanOp = cleanDisplayText(opLabel)
        let badgeText = bounds.width < 290
            ? "🎯 fmu: \(cleanTarget)\(cleanOp.isEmpty ? "" : " · \(cleanOp)")"
            : "🎯 fmu Working Here: \(cleanTarget)\(cleanOp.isEmpty ? "" : " · \(cleanOp)")"
        let pStyle = NSMutableParagraphStyle()
        pStyle.lineBreakMode = .byTruncatingTail
        pStyle.alignment = .center
        let badgeAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11.5, weight: .bold),
            .foregroundColor: NSColor.white,
            .paragraphStyle: pStyle
        ]
        let badgeStr = NSAttributedString(string: badgeText, attributes: badgeAttrs)
        let badgeSz = badgeStr.size()
        let badgeW = min(max(80, bounds.width - 20), ceil(badgeSz.width) + 22)
        let badgeH: CGFloat = 24
        let badgeRect = NSRect(x: max(10, (bounds.width - badgeW) / 2.0), y: 8, width: badgeW, height: badgeH)
        let badgeBg = NSBezierPath(roundedRect: badgeRect, xRadius: 7, yRadius: 7)
        NSColor(calibratedRed: 0.06, green: 0.36, blue: 0.17, alpha: 0.96).setFill()
        badgeBg.fill()
        badgeBg.lineWidth = 1.3
        NSColor(calibratedRed: 0.28, green: 0.98, blue: 0.50, alpha: 0.96).setStroke()
        badgeBg.stroke()
        let textDrawRect = NSRect(
            x: badgeRect.minX + 10,
            y: badgeRect.minY + (badgeH - badgeSz.height) / 2.0,
            width: badgeRect.width - 20,
            height: ceil(badgeSz.height) + 2
        )
        badgeStr.draw(in: textDrawRect)

        // Target reticle + action label at virtual cursor position inside the window
        if let pt = cursorLocalPt, pt.x >= 0, pt.y >= 0, pt.x <= bounds.width, pt.y <= bounds.height {
            let ringRadius: CGFloat = 13.0 + CGFloat(pulsePhase % 3) * 4.0
            let ring = NSBezierPath(ovalIn: NSRect(x: pt.x - ringRadius, y: pt.y - ringRadius, width: ringRadius * 2, height: ringRadius * 2))
            ring.lineWidth = 2.2
            NSColor(calibratedRed: 0.20, green: 0.96, blue: 0.46, alpha: 0.88).setStroke()
            ring.stroke()

            let dot = NSBezierPath(ovalIn: NSRect(x: pt.x - 5, y: pt.y - 5, width: 10, height: 10))
            NSColor(calibratedRed: 0.20, green: 0.96, blue: 0.46, alpha: 1.0).setFill()
            dot.fill()
            NSColor.white.setStroke()
            dot.lineWidth = 1.4
            dot.stroke()

            let cleanCurLbl = cleanDisplayText(cursorLabel)
            let tagText = cleanCurLbl.isEmpty ? cleanOp : (cleanOp.isEmpty ? cleanCurLbl : "\(cleanOp): \(cleanCurLbl)")
            if !tagText.isEmpty {
                let tagP = NSMutableParagraphStyle()
                tagP.lineBreakMode = .byTruncatingTail
                let tagAttrs: [NSAttributedString.Key: Any] = [
                    .font: NSFont.systemFont(ofSize: 10.5, weight: .bold),
                    .foregroundColor: NSColor.white,
                    .paragraphStyle: tagP
                ]
                let tagStr = NSAttributedString(string: tagText, attributes: tagAttrs)
                let tagSz = tagStr.size()
                let tagW = min(max(60, bounds.width - 24), ceil(tagSz.width) + 14)
                let tagH: CGFloat = 19
                let offset = ringRadius + 4.0
                let tx: CGFloat
                let ty: CGFloat
                if pt.x + offset + tagW <= bounds.width - 10 {
                    tx = pt.x + offset
                    ty = min(bounds.height - tagH - 10, max(36, pt.y - tagH / 2))
                } else if pt.x - offset - tagW >= 10 {
                    tx = pt.x - offset - tagW
                    ty = min(bounds.height - tagH - 10, max(36, pt.y - tagH / 2))
                } else {
                    tx = min(bounds.width - tagW - 10, max(10, pt.x - tagW / 2))
                    ty = max(36, pt.y - offset - tagH)
                }
                let tagRect = NSRect(x: tx, y: ty, width: tagW, height: tagH)
                let tagBg = NSBezierPath(roundedRect: tagRect, xRadius: 5, yRadius: 5)
                NSColor(calibratedRed: 0.06, green: 0.38, blue: 0.18, alpha: 0.95).setFill()
                tagBg.fill()
                tagBg.lineWidth = 1.0
                NSColor(calibratedRed: 0.30, green: 0.98, blue: 0.52, alpha: 0.9).setStroke()
                tagBg.stroke()
                tagStr.draw(in: NSRect(x: tagRect.minX + 7, y: tagRect.minY + (tagH - tagSz.height) / 2.0, width: tagW - 14, height: ceil(tagSz.height) + 2))
            }
        }
    }
}

final class FMUHudView: NSView {
    var state: String = "idle"
    var isLiveInFlight: Bool = false
    var elapsedSec: Double = 0.0
    var stepCount: Int = 0
    var surface: String = "idle"
    var target: String = ""
    var op: String = ""
    var spinnerPhase: Int = 0
    var justResumed: Bool = false
    var showPreview: Bool = false
    var isZoomedPreview: Bool = false
    var highlightActive: Bool = false
    var isHeadlessWeb: Bool = false
    var previewImage: CGImage?
    var coordRect: CGRect = CGRect(x: 0, y: 0, width: 1280, height: 800)
    var cursorPt: CGPoint?
    var cursorLabel: String = ""
    var isHovered: Bool = false
    var isStopHovered: Bool = false
    var isCloseHovered: Bool = false
    var isHighlightHovered: Bool = false
    var isTogglePreviewHovered: Bool = false
    var isZoomHovered: Bool = false
    var isPreviewHovered: Bool = false
    fileprivate var closeRect: NSRect = .zero
    fileprivate var highlightBtnRect: NSRect = .zero
    fileprivate var togglePreviewBtnRect: NSRect = .zero
    fileprivate var zoomBtnRect: NSRect = .zero
    fileprivate var stopBtnRect: NSRect = .zero
    fileprivate var previewStageRect: NSRect = .zero
    private var trackingArea: NSTrackingArea?
    private var dragStartMouseScreen: NSPoint?
    private var dragStartWinOrigin: NSPoint?
    private var didDragHud: Bool = false

    override var isFlipped: Bool { false }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        return true
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let existing = trackingArea {
            removeTrackingArea(existing)
        }
        let area = NSTrackingArea(
            rect: bounds,
            options: [.mouseEnteredAndExited, .mouseMoved, .activeAlways, .inVisibleRect],
            owner: self,
            userInfo: nil
        )
        addTrackingArea(area)
        trackingArea = area
    }

    override func resetCursorRects() {
        super.resetCursorRects()
        addCursorRect(bounds, cursor: .pointingHand)
    }

    override func mouseEntered(with event: NSEvent) {
        isHovered = true
        NSCursor.pointingHand.set()
        needsDisplay = true
    }

    override func mouseMoved(with event: NSEvent) {
        let pt = convert(event.locationInWindow, from: nil)
        let overClose = closeRect.width > 0 && closeRect.insetBy(dx: -4, dy: -4).contains(pt)
        let overStop = (state == "working") && stopBtnRect.insetBy(dx: -2, dy: -2).contains(pt)
        let overHl = (state == "working") && highlightBtnRect.insetBy(dx: -2, dy: -2).contains(pt)
        let overToggle = (state == "working") && togglePreviewBtnRect.insetBy(dx: -2, dy: -2).contains(pt)
        let overZoom = (state == "working" && showPreview) && zoomBtnRect.insetBy(dx: -2, dy: -2).contains(pt)
        let overPrev = (state == "working" && showPreview) && previewStageRect.contains(pt)
        if overClose != isCloseHovered || overStop != isStopHovered || overHl != isHighlightHovered || overToggle != isTogglePreviewHovered || overZoom != isZoomHovered || overPrev != isPreviewHovered || !isHovered {
            isHovered = true
            isCloseHovered = overClose
            isStopHovered = overStop
            isHighlightHovered = overHl
            isTogglePreviewHovered = overToggle
            isZoomHovered = overZoom
            isPreviewHovered = overPrev
            needsDisplay = true
        }
    }

    override func mouseExited(with event: NSEvent) {
        isHovered = false
        isStopHovered = false
        isCloseHovered = false
        isHighlightHovered = false
        isTogglePreviewHovered = false
        isZoomHovered = false
        isPreviewHovered = false
        NSCursor.arrow.set()
        needsDisplay = true
    }

    override func mouseDown(with event: NSEvent) {
        didDragHud = false
        dragStartMouseScreen = NSEvent.mouseLocation
        dragStartWinOrigin = window?.frame.origin
    }

    override func mouseDragged(with event: NSEvent) {
        guard let startMouse = dragStartMouseScreen,
              let startOrigin = dragStartWinOrigin,
              let win = window else { return }
        let cur = NSEvent.mouseLocation
        let dx = cur.x - startMouse.x
        let dy = cur.y - startMouse.y
        if !didDragHud && hypot(dx, dy) < 4.0 {
            return
        }
        didDragHud = true
        let newOrigin = NSPoint(x: startOrigin.x + dx, y: startOrigin.y + dy)
        win.setFrameOrigin(newOrigin)
        MenuBarController.shared.setCustomHudOrigin(newOrigin)
    }

    override func mouseUp(with event: NSEvent) {
        let wasDrag = didDragHud
        dragStartMouseScreen = nil
        dragStartWinOrigin = nil
        didDragHud = false
        if wasDrag { return }

        let pt = convert(event.locationInWindow, from: nil)
        if closeRect.width > 0 && closeRect.insetBy(dx: -4, dy: -4).contains(pt) {
            if state == "stopped" {
                MenuBarController.shared.dismissStoppedHud()
            } else {
                MenuBarController.shared.dismissHoldingHud()
            }
            return
        }
        if state == "working" {
            if stopBtnRect.insetBy(dx: -3, dy: -3).contains(pt) {
                MenuBarController.shared.performPrimaryClick()
                return
            }
            if togglePreviewBtnRect.insetBy(dx: -3, dy: -3).contains(pt) {
                MenuBarController.shared.toggleLivePreview()
                return
            }
            if showPreview && zoomBtnRect.width > 0 && zoomBtnRect.insetBy(dx: -3, dy: -3).contains(pt) {
                MenuBarController.shared.toggleZoomPreview()
                return
            }
            if highlightBtnRect.insetBy(dx: -3, dy: -3).contains(pt) {
                MenuBarController.shared.triggerHighlightWindow()
                return
            }
            if showPreview && previewStageRect.contains(pt) {
                if event.clickCount >= 2 {
                    MenuBarController.shared.toggleZoomPreview()
                } else {
                    MenuBarController.shared.triggerHighlightWindow()
                }
                return
            }
            MenuBarController.shared.triggerHighlightWindow()
            return
        }
        if state == "stopped" {
            MenuBarController.shared.performPrimaryClick()
        }
    }

    override func rightMouseUp(with event: NSEvent) {
        MenuBarController.shared.showContextMenuFromHud(event: event, in: self)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        ctx.saveGState()
        defer { ctx.restoreGState() }

        let boundsRect = bounds.insetBy(dx: 1.0, dy: 1.0)
        let bgPath = NSBezierPath(roundedRect: boundsRect, xRadius: 12, yRadius: 12)

        // Deep charcoal glass background
        let bgWhite: CGFloat = (isHovered && state == "stopped") ? 0.13 : 0.09
        NSColor(calibratedRed: bgWhite, green: bgWhite + 0.01, blue: bgWhite + 0.03, alpha: 0.97).setFill()
        bgPath.fill()

        // High-contrast status border
        bgPath.lineWidth = highlightActive ? 2.0 : (isHovered ? 1.8 : 1.5)
        if state == "stopped" {
            NSColor(calibratedRed: 1.0, green: 0.27, blue: 0.23, alpha: 0.95).setStroke()
        } else if state == "working" {
            let borderAlpha: CGFloat = highlightActive ? 1.0 : 0.92
            NSColor(calibratedRed: 0.20, green: 0.88, blue: 0.40, alpha: borderAlpha).setStroke()
        } else {
            NSColor(calibratedWhite: 0.45, alpha: 0.65).setStroke()
        }
        bgPath.stroke()

        // Top header bar is always 44pt tall at the top of the HUD card
        let headerMidY = bounds.maxY - 22.0
        let iconCenter = CGPoint(x: 21, y: headerMidY)

        if state == "stopped" {
            let circle = NSBezierPath(ovalIn: NSRect(x: iconCenter.x - 8, y: iconCenter.y - 8, width: 16, height: 16))
            NSColor(calibratedRed: 1.0, green: 0.23, blue: 0.19, alpha: 1.0).setFill()
            circle.fill()
            let sq = NSBezierPath(roundedRect: NSRect(x: iconCenter.x - 3.5, y: iconCenter.y - 3.5, width: 7, height: 7), xRadius: 1.2, yRadius: 1.2)
            NSColor.white.setFill()
            sq.fill()
        } else if state == "working" {
            let track = NSBezierPath(ovalIn: NSRect(x: iconCenter.x - 8, y: iconCenter.y - 8, width: 16, height: 16))
            track.lineWidth = 2.0
            NSColor(calibratedRed: 0.19, green: 0.82, blue: 0.35, alpha: 0.25).setStroke()
            track.stroke()

            let startDeg = CGFloat((spinnerPhase * 45) % 360)
            let endDeg = startDeg + 230.0
            let arc = NSBezierPath()
            arc.appendArc(withCenter: iconCenter, radius: 8.0, startAngle: startDeg, endAngle: endDeg, clockwise: false)
            arc.lineWidth = 2.2
            arc.lineCapStyle = .round
            NSColor(calibratedRed: 0.22, green: 0.92, blue: 0.42, alpha: 1.0).setStroke()
            arc.stroke()

            let dot = NSBezierPath(ovalIn: NSRect(x: iconCenter.x - 3.5, y: iconCenter.y - 3.5, width: 7, height: 7))
            NSColor(calibratedRed: 0.22, green: 0.92, blue: 0.42, alpha: 1.0).setFill()
            dot.fill()
        } else {
            let circle = NSBezierPath(ovalIn: NSRect(x: iconCenter.x - 7, y: iconCenter.y - 7, width: 14, height: 14))
            NSColor(calibratedRed: 0.19, green: 0.82, blue: 0.35, alpha: 0.9).setFill()
            circle.fill()
        }

        // Right CTA Pill Buttons in Header
        closeRect = .zero
        highlightBtnRect = .zero
        togglePreviewBtnRect = .zero
        zoomBtnRect = .zero
        stopBtnRect = .zero
        previewStageRect = .zero

        var rightEdge = bounds.maxX - 9
        var leftOfButtons = rightEdge
        let btnH: CGFloat = 25

        if state == "stopped" || (state == "working" && !isLiveInFlight) {
            let xRect = NSRect(x: rightEdge - 20, y: headerMidY - 10, width: 20, height: 20)
            closeRect = xRect
            if isCloseHovered {
                let xBg = NSBezierPath(ovalIn: xRect)
                NSColor(calibratedWhite: 1.0, alpha: 0.18).setFill()
                xBg.fill()
            }
            let xAttr: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 11, weight: .bold),
                .foregroundColor: NSColor(calibratedWhite: isCloseHovered ? 1.0 : 0.72, alpha: 0.95)
            ]
            let xStr = NSAttributedString(string: "✕", attributes: xAttr)
            let xSz = xStr.size()
            xStr.draw(at: NSPoint(x: xRect.midX - xSz.width / 2, y: xRect.midY - xSz.height / 2))
            rightEdge = xRect.minX - 6
            leftOfButtons = rightEdge
        }

        if state == "stopped" || state == "working" {
            let btnText: String
            let btnColor: NSColor
            if state == "stopped" {
                btnText = "▶ Click to Resume"
                btnColor = (isHovered && !isCloseHovered)
                    ? NSColor(calibratedRed: 0.20, green: 0.78, blue: 0.35, alpha: 1.0)
                    : NSColor(calibratedRed: 0.16, green: 0.68, blue: 0.29, alpha: 1.0)
            } else {
                btnText = "⏹ Stop"
                btnColor = isStopHovered
                    ? NSColor(calibratedRed: 1.0, green: 0.28, blue: 0.24, alpha: 1.0)
                    : NSColor(calibratedRed: 0.92, green: 0.20, blue: 0.18, alpha: 1.0)
            }

            let btnFont = NSFont.systemFont(ofSize: 11.0, weight: .bold)
            let btnAttrs: [NSAttributedString.Key: Any] = [
                .font: btnFont,
                .foregroundColor: NSColor.white
            ]
            let btnAttrStr = NSAttributedString(string: btnText, attributes: btnAttrs)
            let btnTextSz = btnAttrStr.size()
            let btnW = ceil(btnTextSz.width) + 16
            let btnRect = NSRect(x: rightEdge - btnW, y: headerMidY - btnH / 2, width: btnW, height: btnH)
            stopBtnRect = btnRect
            let btnPath = NSBezierPath(roundedRect: btnRect, xRadius: 6.5, yRadius: 6.5)
            btnColor.setFill()
            btnPath.fill()
            if isStopHovered {
                btnPath.lineWidth = 1.2
                NSColor(calibratedWhite: 1.0, alpha: 0.5).setStroke()
                btnPath.stroke()
            }
            btnAttrStr.draw(at: NSPoint(x: btnRect.midX - btnTextSz.width / 2, y: btnRect.midY - btnTextSz.height / 2))
            leftOfButtons = btnRect.minX - 5
        }

        // Secondary '🎯 Highlight' + '⤢ Zoom' + '▾/▴' Preview Toggle buttons when working
        if state == "working" {
            // 1. Compact preview expand/collapse toggle button (vector chevron)
            let togW: CGFloat = 24
            let togRect = NSRect(x: leftOfButtons - togW, y: headerMidY - btnH / 2, width: togW, height: btnH)
            togglePreviewBtnRect = togRect
            let togPath = NSBezierPath(roundedRect: togRect, xRadius: 6, yRadius: 6)
            NSColor(calibratedWhite: isTogglePreviewHovered ? 0.32 : 0.20, alpha: 0.88).setFill()
            togPath.fill()
            togPath.lineWidth = 1.0
            NSColor(calibratedWhite: isTogglePreviewHovered ? 0.65 : 0.38, alpha: 0.85).setStroke()
            togPath.stroke()

            let chev = NSBezierPath()
            let cx = togRect.midX
            let cy = togRect.midY
            if showPreview {
                chev.move(to: NSPoint(x: cx - 4.2, y: cy - 1.8))
                chev.line(to: NSPoint(x: cx, y: cy + 2.4))
                chev.line(to: NSPoint(x: cx + 4.2, y: cy - 1.8))
            } else {
                chev.move(to: NSPoint(x: cx - 4.2, y: cy + 1.8))
                chev.line(to: NSPoint(x: cx, y: cy - 2.4))
                chev.line(to: NSPoint(x: cx + 4.2, y: cy + 1.8))
            }
            chev.lineWidth = 1.8
            chev.lineCapStyle = .round
            chev.lineJoinStyle = .round
            NSColor(calibratedWhite: 0.94, alpha: 1.0).setStroke()
            chev.stroke()
            leftOfButtons = togRect.minX - 5

            // 2. HD Large Monitor Zoom toggle ('⤢' / '⤡') when preview is expanded
            if showPreview {
                let zmW: CGFloat = 24
                let zmRect = NSRect(x: leftOfButtons - zmW, y: headerMidY - btnH / 2, width: zmW, height: btnH)
                zoomBtnRect = zmRect
                let zmPath = NSBezierPath(roundedRect: zmRect, xRadius: 6, yRadius: 6)
                let zmFillWhite: CGFloat = isZoomedPreview ? 0.28 : (isZoomHovered ? 0.32 : 0.20)
                NSColor(calibratedWhite: zmFillWhite, alpha: 0.90).setFill()
                zmPath.fill()
                zmPath.lineWidth = isZoomedPreview ? 1.2 : 1.0
                if isZoomedPreview {
                    NSColor(calibratedRed: 0.28, green: 0.92, blue: 0.48, alpha: 0.85).setStroke()
                } else {
                    NSColor(calibratedWhite: isZoomHovered ? 0.65 : 0.38, alpha: 0.85).setStroke()
                }
                zmPath.stroke()

                let zmSym = isZoomedPreview ? "⤡" : "⤢"
                let zmAttrs: [NSAttributedString.Key: Any] = [
                    .font: NSFont.systemFont(ofSize: 12.0, weight: .bold),
                    .foregroundColor: NSColor.white
                ]
                let zmStr = NSAttributedString(string: zmSym, attributes: zmAttrs)
                let zmSz = zmStr.size()
                zmStr.draw(at: NSPoint(x: zmRect.midX - zmSz.width / 2, y: zmRect.midY - zmSz.height / 2))
                leftOfButtons = zmRect.minX - 5
            }

            // 3. '🎯 Highlight' button
            let hlText = highlightActive ? "🎯 Active" : "🎯 Highlight"
            let hlAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 10.5, weight: .semibold),
                .foregroundColor: NSColor.white
            ]
            let hlStr = NSAttributedString(string: hlText, attributes: hlAttrs)
            let hlSz = hlStr.size()
            let hlW = ceil(hlSz.width) + 12
            let hlRect = NSRect(x: leftOfButtons - hlW, y: headerMidY - btnH / 2, width: hlW, height: btnH)
            highlightBtnRect = hlRect
            let hlPath = NSBezierPath(roundedRect: hlRect, xRadius: 6, yRadius: 6)
            let hlAlpha: CGFloat = highlightActive ? 0.48 : (isHighlightHovered ? 0.36 : 0.22)
            NSColor(calibratedRed: 0.16, green: 0.78, blue: 0.38, alpha: hlAlpha).setFill()
            hlPath.fill()
            hlPath.lineWidth = highlightActive ? 1.4 : 1.0
            NSColor(calibratedRed: 0.25, green: 0.95, blue: 0.48, alpha: highlightActive ? 0.98 : 0.75).setStroke()
            hlPath.stroke()
            hlStr.draw(at: NSPoint(x: hlRect.midX - hlSz.width / 2, y: hlRect.midY - hlSz.height / 2))
            leftOfButtons = hlRect.minX - 6
        }

        // Center 2-line title & subtitle in Header
        let textLeft: CGFloat = 36
        let maxTextW = max(70, leftOfButtons - textLeft)

        let titleStr: String
        let subStr: String
        if state == "stopped" {
            titleStr = "fmu Agent Stopped"
            subStr = "All agent actions blocked"
        } else if state == "working" {
            if isLiveInFlight && elapsedSec >= 0.15 {
                titleStr = String(format: "fmu Working (%.1fs)", elapsedSec)
            } else if isLiveInFlight {
                titleStr = "fmu Working · Live"
            } else {
                titleStr = stepCount > 1 ? "fmu Live View · \(stepCount) steps" : "fmu Live View"
            }
            let surfLabel = (surface == "web" || surface == "browser") ? "Web" : "App"
            let tgt = cleanDisplayText(target.isEmpty ? (surfLabel == "Web" ? "Browser" : "macOS") : target)
            let opLabel = cleanDisplayText(op.isEmpty ? "work" : op)
            subStr = "\(surfLabel): \(tgt) → \(opLabel)"
        } else {
            titleStr = "fmu Agent Resumed"
            subStr = "Ready (Idle)"
        }

        let pStyle = NSMutableParagraphStyle()
        pStyle.lineBreakMode = .byTruncatingTail

        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11.5, weight: .bold),
            .foregroundColor: NSColor.white,
            .paragraphStyle: pStyle
        ]
        let subAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 10.0, weight: .medium),
            .foregroundColor: NSColor(calibratedWhite: 0.78, alpha: 0.95),
            .paragraphStyle: pStyle
        ]

        NSAttributedString(string: titleStr, attributes: titleAttrs).draw(
            in: NSRect(x: textLeft, y: headerMidY + 1, width: maxTextW, height: 15)
        )
        NSAttributedString(string: subStr, attributes: subAttrs).draw(
            in: NSRect(x: textLeft, y: headerMidY - 14, width: maxTextW, height: 14)
        )

        // Real-Time Pixel Live Screen Stage below Header when Working
        if state == "working" && showPreview && bounds.height > 80 {
            let stage = NSRect(x: 8, y: 8, width: bounds.width - 16, height: bounds.height - 50)
            previewStageRect = stage
            let stagePath = NSBezierPath(roundedRect: stage, xRadius: 8, yRadius: 8)
            NSColor(calibratedRed: 0.04, green: 0.05, blue: 0.07, alpha: 0.99).setFill()
            stagePath.fill()
            stagePath.lineWidth = (highlightActive || isPreviewHovered) ? 1.4 : 1.0
            if highlightActive {
                NSColor(calibratedRed: 0.25, green: 0.96, blue: 0.48, alpha: 0.95).setStroke()
            } else {
                NSColor(calibratedWhite: isPreviewHovered ? 0.55 : 0.25, alpha: 0.85).setStroke()
            }
            stagePath.stroke()

            let captionH: CGFloat = 18
            let viewport = NSRect(
                x: stage.minX + 6,
                y: stage.minY + captionH + 4,
                width: stage.width - 12,
                height: stage.height - captionH - 10
            )

            // Aspect-fit using actual CGImage dimensions when available so pixel proportions are 100% exact
            let imgAspectW: CGFloat
            let imgAspectH: CGFloat
            if let cgImg = previewImage, cgImg.width > 0, cgImg.height > 0 {
                imgAspectW = CGFloat(cgImg.width)
                imgAspectH = CGFloat(cgImg.height)
            } else {
                imgAspectW = max(120.0, coordRect.width)
                imgAspectH = max(90.0, coordRect.height)
            }
            let scale = min(viewport.width / imgAspectW, viewport.height / imgAspectH)
            let drawW = max(80.0, floor(imgAspectW * scale))
            let drawH = max(54.0, floor(imgAspectH * scale))
            let winDrawRect = NSRect(
                x: floor(viewport.midX - drawW / 2.0),
                y: floor(viewport.midY - drawH / 2.0),
                width: drawW,
                height: drawH
            )

            if let cgImg = previewImage {
                ctx.saveGState()
                let clipPath = NSBezierPath(roundedRect: winDrawRect, xRadius: 5, yRadius: 5)
                clipPath.addClip()
                ctx.interpolationQuality = .high
                ctx.draw(cgImg, in: winDrawRect)
                ctx.restoreGState()

                let winFramePath = NSBezierPath(roundedRect: winDrawRect, xRadius: 5, yRadius: 5)
                winFramePath.lineWidth = 1.0
                NSColor(calibratedWhite: 0.58, alpha: 0.45).setStroke()
                winFramePath.stroke()

                // Subtle top-left LIVE stream badge
                let liveBadgeText = isLiveInFlight ? "● LIVE" : "● LIVE HOLD"
                let liveAttrs: [NSAttributedString.Key: Any] = [
                    .font: NSFont.monospacedSystemFont(ofSize: 8.5, weight: .bold),
                    .foregroundColor: isLiveInFlight
                        ? NSColor(calibratedRed: 0.35, green: 1.0, blue: 0.55, alpha: 1.0)
                        : NSColor(calibratedWhite: 0.88, alpha: 0.92)
                ]
                let liveStr = NSAttributedString(string: liveBadgeText, attributes: liveAttrs)
                let liveSz = liveStr.size()
                let liveRect = NSRect(
                    x: winDrawRect.minX + 6,
                    y: winDrawRect.maxY - liveSz.height - 8,
                    width: ceil(liveSz.width) + 10,
                    height: ceil(liveSz.height) + 4
                )
                let liveBg = NSBezierPath(roundedRect: liveRect, xRadius: 4, yRadius: 4)
                NSColor(calibratedRed: 0.04, green: 0.06, blue: 0.09, alpha: 0.78).setFill()
                liveBg.fill()
                liveStr.draw(at: NSPoint(x: liveRect.minX + 5, y: liveRect.minY + 2))

                // Draw Virtual Cursor & pulsing target reticle ONLY when an actual target coordinate exists
                let coordW = max(1.0, coordRect.width)
                let coordH = max(1.0, coordRect.height)
                if let cpt = cursorPt {
                    let rawX = (cpt.x - coordRect.origin.x) / coordW
                    let rawY = (cpt.y - coordRect.origin.y) / coordH
                    if rawX >= -0.02 && rawX <= 1.02 && rawY >= -0.02 && rawY <= 1.02 {
                        let normX = min(0.98, max(0.02, rawX))
                        let normY = min(0.98, max(0.02, rawY))
                        let curX = winDrawRect.minX + normX * winDrawRect.width
                        let curY = winDrawRect.maxY - normY * winDrawRect.height

                        let pulseR: CGFloat = 7.0 + CGFloat(spinnerPhase % 4) * 2.6
                        let pulseRing = NSBezierPath(ovalIn: NSRect(x: curX - pulseR, y: curY - pulseR, width: pulseR * 2, height: pulseR * 2))
                        pulseRing.lineWidth = 1.8
                        NSColor(calibratedRed: 0.20, green: 0.96, blue: 0.46, alpha: 0.88).setStroke()
                        pulseRing.stroke()

                        let curDot = NSBezierPath(ovalIn: NSRect(x: curX - 4.0, y: curY - 4.0, width: 8.0, height: 8.0))
                        NSColor(calibratedRed: 0.18, green: 0.95, blue: 0.42, alpha: 1.0).setFill()
                        curDot.fill()
                        NSColor.white.setStroke()
                        curDot.lineWidth = 1.3
                        curDot.stroke()

                        let cleanCurLbl = cleanDisplayText(cursorLabel)
                        let cleanOp = cleanDisplayText(op)
                        let tagText = cleanCurLbl.isEmpty ? cleanOp : (cleanOp.isEmpty ? cleanCurLbl : "\(cleanOp): \(cleanCurLbl)")
                        if !tagText.isEmpty {
                            let shortTag = tagText.count > 26 ? String(tagText.prefix(25)) + "…" : tagText
                            let tagAttrs: [NSAttributedString.Key: Any] = [
                                .font: NSFont.systemFont(ofSize: 9.5, weight: .bold),
                                .foregroundColor: NSColor.white
                            ]
                            let tagStr = NSAttributedString(string: shortTag, attributes: tagAttrs)
                            let tagSz = tagStr.size()
                            let tagW = ceil(tagSz.width) + 10
                            let tagH: CGFloat = 15
                            let offset = pulseR + 3.0
                            let tx: CGFloat
                            let ty: CGFloat
                            if curX + offset + tagW <= stage.maxX - 4 {
                                tx = curX + offset
                                ty = min(viewport.maxY - tagH - 2, max(viewport.minY + 2, curY - tagH / 2))
                            } else if curX - offset - tagW >= stage.minX + 4 {
                                tx = curX - offset - tagW
                                ty = min(viewport.maxY - tagH - 2, max(viewport.minY + 2, curY - tagH / 2))
                            } else {
                                tx = min(stage.maxX - tagW - 4, max(stage.minX + 4, curX - tagW / 2))
                                ty = min(viewport.maxY - tagH - 2, max(viewport.minY + 2, curY + offset))
                            }
                            let tagRect = NSRect(x: tx, y: ty, width: tagW, height: tagH)
                            let tagBg = NSBezierPath(roundedRect: tagRect, xRadius: 4, yRadius: 4)
                            NSColor(calibratedRed: 0.06, green: 0.38, blue: 0.18, alpha: 0.94).setFill()
                            tagBg.fill()
                            tagBg.lineWidth = 0.8
                            NSColor(calibratedRed: 0.28, green: 0.96, blue: 0.48, alpha: 0.85).setStroke()
                            tagBg.stroke()
                            tagStr.draw(at: NSPoint(x: tagRect.minX + 5, y: tagRect.minY + (tagH - tagSz.height) / 2))
                        }
                    }
                }
            } else {
                // Honest waiting state while first live pixel frame arrives (no fake wireframe mock)
                let emptyRect = viewport.insetBy(dx: 4, dy: 4)
                let emptyPath = NSBezierPath(roundedRect: emptyRect, xRadius: 6, yRadius: 6)
                NSColor(calibratedRed: 0.07, green: 0.09, blue: 0.12, alpha: 1.0).setFill()
                emptyPath.fill()
                emptyPath.lineWidth = 1.0
                NSColor(calibratedWhite: 0.30, alpha: 0.50).setStroke()
                emptyPath.stroke()

                let msg: String
                if surface == "app" && !CGPreflightScreenCaptureAccess() {
                    msg = "Grant Screen Recording permission to fmu-ax for live native app pixels"
                } else {
                    let tgt = cleanDisplayText(target.isEmpty ? "screen" : target)
                    msg = "Connecting live pixel stream for \(tgt)…"
                }
                let mStyle = NSMutableParagraphStyle()
                mStyle.alignment = .center
                let mAttrs: [NSAttributedString.Key: Any] = [
                    .font: NSFont.systemFont(ofSize: 11.0, weight: .medium),
                    .foregroundColor: NSColor(calibratedWhite: 0.72, alpha: 0.92),
                    .paragraphStyle: mStyle
                ]
                let mStr = NSAttributedString(string: msg, attributes: mAttrs)
                let mSz = mStr.size()
                mStr.draw(
                    in: NSRect(
                        x: emptyRect.minX + 12,
                        y: emptyRect.midY - mSz.height / 2,
                        width: max(40, emptyRect.width - 24),
                        height: mSz.height + 4
                    )
                )
            }

            // Bottom caption strip inside PiP stage
            let capLeftAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 9.5, weight: .semibold),
                .foregroundColor: NSColor(calibratedWhite: 0.78, alpha: 0.95)
            ]
            let capRightAttrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.systemFont(ofSize: 9.5, weight: (highlightActive || isPreviewHovered) ? .semibold : .medium),
                .foregroundColor: (highlightActive || isPreviewHovered)
                    ? NSColor(calibratedRed: 0.32, green: 0.98, blue: 0.54, alpha: 1.0)
                    : NSColor(calibratedWhite: 0.62, alpha: 0.90)
            ]
            let leftTitle: String
            if surface == "web" || surface == "browser" {
                leftTitle = isHeadlessWeb ? "👻 Headless Tab Stream" : "📺 Live Tab Stream"
            } else {
                leftTitle = "📺 Live App Window"
            }
            let rightTitle = highlightActive
                ? (isHeadlessWeb ? "✨ Active in Headless Stream" : "✨ Highlighted on screen!")
                : (isZoomedPreview ? "🎯 Click to highlight · ⤡ Compact" : "🎯 Click to highlight · ⤢ Expand HD")
            let leftCap = NSAttributedString(string: leftTitle, attributes: capLeftAttrs)
            let rightCap = NSAttributedString(string: rightTitle, attributes: capRightAttrs)
            leftCap.draw(at: NSPoint(x: stage.minX + 8, y: stage.minY + 3))
            let rcSz = rightCap.size()
            rightCap.draw(at: NSPoint(x: stage.maxX - rcSz.width - 8, y: stage.minY + 3))
        }
    }
}

final class MenuBarController: NSObject {
    static let shared = MenuBarController()

    let statusLock = NSLock()
    var statusItem: NSStatusItem?
    var hudPanel: FMUHudPanel?
    var hudView: FMUHudView?
    var highlightPanel: FMUHudPanel?
    var highlightView: FMUHighlightView?
    var pollTimer: Timer?
    var daemonSocketPath: String = "/tmp/fmu-ax.sock"
    let previewQueue = DispatchQueue(label: "fmu.menubar.preview", qos: .utility)

    private var _isStopped: Bool = false
    private var _activeCount: Int = 0
    private var _workStartTime: Double = 0
    private var _lastActiveTime: Double = 0
    private var _justResumedUntil: Double = 0
    private var _highlightUntil: Double = 0
    private var _hudDismissedWhileStopped: Bool = false
    private var _hudDismissedWhileHolding: Bool = false
    private var _showLivePreview: Bool = (ProcessInfo.processInfo.environment["FMU_PREVIEW"] != "0")
    private var _zoomedPreview: Bool = false
    private var _customHudOrigin: NSPoint? = nil
    private var _surface: String = "idle"
    private var _target: String = ""
    private var _op: String = ""
    private var _agent: String = "default"
    private var _stopReason: String = ""
    private var _lastRenderedSignature: String = ""
    private var _previewGeneration: UInt64 = 0
    private var _spinnerPhase: Int = 0
    private var _idleTicks: Int = 0

    private var _targetPid: pid_t = 0
    private var _targetWindowID: CGWindowID = 0
    private var _targetWinRect: CGRect = CGRect(x: 140, y: 90, width: 900, height: 600)
    private var _previewCoordRect: CGRect = CGRect(x: 0, y: 0, width: 900, height: 600)
    private var _isHeadlessWeb: Bool = false
    private var _lastStepCount: Int = 1
    private var _cursorPt: CGPoint?
    private var _cursorLabel: String = ""
    private var _previewImage: CGImage?
    private var _previewCaptureInFlight: Bool = false
    private var _previewForcePending: Bool = false
    private var _lastCaptureTime: Double = 0

    var activeHoldSeconds: Double {
        if let raw = ProcessInfo.processInfo.environment["FMU_ACTIVE_HOLD_SEC"], let val = Double(raw), val >= 0.5 {
            return val
        }
        return 12.0
    }

    var maxInFlightSeconds: Double {
        if let raw = ProcessInfo.processInfo.environment["FMU_MAX_INFLIGHT_SEC"], let val = Double(raw), val >= 1.0 {
            return val
        }
        return 20.0
    }

    var stopFilePath: String {
        return ProcessInfo.processInfo.environment["FMU_STOP_FILE"] ?? "/tmp/fmu-stop.json"
    }

    var runtimeSocketPath: String {
        return ProcessInfo.processInfo.environment["FMU_RUNTIME_SOCK"] ?? "/tmp/fmu-runtime.sock"
    }

    func setup(socketPath: String) {
        self.daemonSocketPath = socketPath
        if ProcessInfo.processInfo.environment["FMU_MENUBAR"] == "0" {
            return
        }
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        self.statusItem = item
        if let btn = item.button {
            btn.target = self
            btn.action = #selector(handleStatusBarClick(_:))
            btn.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        // Create non-activating floating top-right HUD pill + Live PiP Mini Window Preview
        let panelRect = NSRect(x: 0, y: 0, width: 368, height: 44)
        let panel = FMUHudPanel(
            contentRect: panelRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.hidesOnDeactivate = false
        panel.isMovableByWindowBackground = false

        let view = FMUHudView(frame: panelRect)
        panel.contentView = view
        self.hudPanel = panel
        self.hudView = view

        // Create non-activating click-through neon highlight frame for Target Window Highlight on screen
        let hlRect = NSRect(x: 140, y: 140, width: 640, height: 480)
        let hlPanel = FMUHudPanel(
            contentRect: hlRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        hlPanel.level = .statusBar
        hlPanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        hlPanel.isOpaque = false
        hlPanel.backgroundColor = .clear
        hlPanel.hasShadow = false
        hlPanel.hidesOnDeactivate = false
        hlPanel.ignoresMouseEvents = true

        let hlView = FMUHighlightView(frame: hlRect)
        hlPanel.contentView = hlView
        self.highlightPanel = hlPanel
        self.highlightView = hlView

        _ = checkStopped()
        applyTitleOnMain(force: true)

        let timer = Timer(timeInterval: 0.14, repeats: true) { [weak self] _ in
            self?.tickTimerOnMain()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.pollTimer = timer
    }

    private func truncateLabel(_ s: String, maxChars: Int = 18) -> String {
        let clean = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if clean.count <= maxChars { return clean }
        return String(clean.prefix(maxChars - 1)) + "…"
    }

    private func computeDisplayLocked(now: Double) -> (state: String, plainTitle: String, surface: String, target: String, op: String, agent: String, stopped: Bool, isLiveInFlight: Bool, justResumed: Bool, hudDismissed: Bool, showPreview: Bool, highlightActive: Bool) {
        let hlActive = (_highlightUntil > now)
        if _isStopped {
            return ("stopped", "🛑 fmu Stopped  ▶ Resume", _surface, _target, _op, _agent, true, false, false, _hudDismissedWhileStopped, false, hlActive)
        }
        if _activeCount > 0 && _lastActiveTime > 0 && (now - _lastActiveTime) > maxInFlightSeconds {
            _activeCount = 0
            _lastActiveTime = 0
        }
        let isWorking = (_activeCount > 0) || (_lastActiveTime > 0 && (now - _lastActiveTime) < activeHoldSeconds)
        if isWorking {
            let shortTarget = truncateLabel(_target.isEmpty ? (_surface == "web" ? "Chrome" : "macOS") : _target, maxChars: 18)
            let shortOp = truncateLabel(_op.isEmpty ? "work" : _op, maxChars: 10)
            let title = "🟢 fmu Working: \(shortTarget) · \(shortOp)  ⏹ Stop"
            let isLive = (_activeCount > 0)
            let dismissed = (!isLive && _hudDismissedWhileHolding)
            return ("working", title, _surface, _target, _op, _agent, false, isLive, false, dismissed, _showLivePreview, hlActive)
        }
        let justResumed = (_justResumedUntil > now)
        return ("idle", "⚪ fmu Idle", "idle", _target, _op, _agent, false, false, justResumed, false, false, hlActive)
    }

    func dismissStoppedHud() {
        statusLock.lock()
        _hudDismissedWhileStopped = true
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    func dismissHoldingHud() {
        statusLock.lock()
        _hudDismissedWhileHolding = true
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    func toggleLivePreview() {
        statusLock.lock()
        _showLivePreview.toggle()
        _previewGeneration &+= 1
        statusLock.unlock()
        if _showLivePreview {
            maybeCaptureWindowThumbnailAsync(force: true)
        }
        refreshUIAsync(immediateIfMain: true)
    }

    func toggleZoomPreview() {
        statusLock.lock()
        _showLivePreview = true
        _zoomedPreview.toggle()
        _previewGeneration &+= 1
        statusLock.unlock()
        maybeCaptureWindowThumbnailAsync(force: true)
        refreshUIAsync(immediateIfMain: true)
    }

    func setCustomHudOrigin(_ origin: NSPoint?) {
        statusLock.lock()
        _customHudOrigin = origin
        _previewGeneration &+= 1
        statusLock.unlock()
    }

    func resetHudPosition() {
        statusLock.lock()
        _customHudOrigin = nil
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    private func resolveQuartzWindowBounds(pid: pid_t, windowID: CGWindowID, targetHint: String, surface: String) -> (wid: CGWindowID, pid: pid_t, rect: CGRect)? {
        guard let infoList = CGWindowListCopyWindowInfo([.optionAll], kCGNullWindowID) as? [[String: Any]] else {
            return nil
        }
        let lowerHint = targetHint.lowercased()
        // 1. Exact windowID match
        if windowID != 0 {
            for entry in infoList {
                let wid = (entry[kCGWindowNumber as String] as? UInt32) ?? 0
                if wid == windowID,
                   let bDict = entry[kCGWindowBounds as String] as? NSDictionary,
                   let r = CGRect(dictionaryRepresentation: bDict), r.width > 80 && r.height > 60 {
                    let ownerPid = (entry[kCGWindowOwnerPID as String] as? pid_t) ?? pid
                    return (wid, ownerPid, r)
                }
            }
        }
        // 2. PID match (pick best/largest real window, avoiding tiny Find bars or floating strips)
        if pid > 0 {
            var best: (wid: CGWindowID, pid: pid_t, rect: CGRect, score: Double)?
            for entry in infoList {
                let ownerPid = (entry[kCGWindowOwnerPID as String] as? pid_t) ?? 0
                let layer = (entry[kCGWindowLayer as String] as? Int) ?? 0
                let wid = (entry[kCGWindowNumber as String] as? UInt32) ?? 0
                guard ownerPid == pid && layer == 0 && wid != 0,
                      let bDict = entry[kCGWindowBounds as String] as? NSDictionary,
                      let r = CGRect(dictionaryRepresentation: bDict), r.width > 80 && r.height > 60 else { continue }
                let winName = (entry[kCGWindowName as String] as? String ?? "").lowercased()
                var score = Double(r.width * r.height)
                if !lowerHint.isEmpty && winName.contains(lowerHint) {
                    score += 5_000_000.0
                }
                if r.width < 200 || r.height < 140 {
                    score *= 0.05
                }
                if best == nil || score > best!.score {
                    best = (wid, ownerPid, r, score)
                }
            }
            if let b = best {
                return (b.wid, b.pid, b.rect)
            }
        }
        // 3. Owner name or Chrome window match (scored so Chrome's 340x82 Find bar is never chosen over the browser window)
        var bestFallback: (wid: CGWindowID, pid: pid_t, rect: CGRect, score: Double)?
        for entry in infoList {
            let layer = (entry[kCGWindowLayer as String] as? Int) ?? 0
            let wid = (entry[kCGWindowNumber as String] as? UInt32) ?? 0
            guard layer == 0 && wid != 0 else { continue }
            let ownerName = (entry[kCGWindowOwnerName as String] as? String ?? "").lowercased()
            let winName = (entry[kCGWindowName as String] as? String ?? "").lowercased()
            let isMatch: Bool
            if surface == "web" || surface == "browser" {
                isMatch = ownerName.contains("chrome") || ownerName.contains("chromium") || (!lowerHint.isEmpty && winName.contains(lowerHint))
            } else {
                isMatch = !lowerHint.isEmpty && (ownerName == lowerHint || ownerName.contains(lowerHint))
            }
            if isMatch,
               let bDict = entry[kCGWindowBounds as String] as? NSDictionary,
               let r = CGRect(dictionaryRepresentation: bDict), r.width > 120 && r.height > 80 {
                let ownerPid = (entry[kCGWindowOwnerPID as String] as? pid_t) ?? 0
                var score = Double(r.width * r.height)
                if !lowerHint.isEmpty && winName.contains(lowerHint) {
                    score += 10_000_000.0
                }
                if r.width < 260 || r.height < 180 {
                    score *= 0.02
                }
                if bestFallback == nil || score > bestFallback!.score {
                    bestFallback = (wid, ownerPid, r, score)
                }
            }
        }
        if let bf = bestFallback {
            return (bf.wid, bf.pid, bf.rect)
        }
        return nil
    }

    func triggerHighlightWindow(duration: Double = 2.2) {
        statusLock.lock()
        let curPid = _targetPid
        let curWid = _targetWindowID
        let curTarget = _target
        let curSurface = _surface
        let headless = _isHeadlessWeb
        statusLock.unlock()

        let resolved = headless ? nil : resolveQuartzWindowBounds(pid: curPid, windowID: curWid, targetHint: curTarget, surface: curSurface)
        statusLock.lock()
        if let res = resolved {
            _targetWindowID = res.wid
            if res.pid > 0 { _targetPid = res.pid }
            _targetWinRect = res.rect
        }
        _highlightUntil = CFAbsoluteTimeGetCurrent() + max(0.5, duration)
        if _lastActiveTime > 0 {
            _lastActiveTime = max(_lastActiveTime, CFAbsoluteTimeGetCurrent())
        }
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    func updateAppCursorOnly(cursor: VirtualCursorState?, op: String? = nil) {
        statusLock.lock()
        if let cur = cursor {
            _cursorPt = CGPoint(x: cur.x, y: cur.y)
            _cursorLabel = cur.label
        }
        if let opStr = op, !opStr.isEmpty {
            _op = opStr
        }
        _lastActiveTime = CFAbsoluteTimeGetCurrent()
        _previewGeneration &+= 1
        statusLock.unlock()
        maybeCaptureWindowThumbnailAsync(force: false)
        refreshUIAsync()
    }

    func updateAppWindowPreview(
        pid: pid_t,
        windowID: CGWindowID,
        appName: String,
        winTitle: String,
        winRect: CGRect,
        nodes: [NodeRecord],
        cursor: VirtualCursorState?,
        forceCapture: Bool = false
    ) {
        statusLock.lock()
        let prevPid = _targetPid
        let prevSurf = _surface
        _targetPid = pid
        _isHeadlessWeb = false
        if windowID != 0 {
            _targetWindowID = windowID
        } else if prevPid != pid {
            _targetWindowID = 0
        }
        if winRect.width > 40 && winRect.height > 40 {
            _targetWinRect = winRect
            _previewCoordRect = winRect
        }
        let switchedApp = (prevPid != pid || prevSurf != "app")
        if switchedApp {
            _previewImage = nil
            if cursor == nil {
                _cursorPt = nil
                _cursorLabel = ""
            }
        }
        if let cur = cursor {
            _cursorPt = CGPoint(x: cur.x, y: cur.y)
            _cursorLabel = cur.label
        }
        let needsInitialFrame = (_previewImage == nil)
        _previewGeneration &+= 1
        statusLock.unlock()
        maybeCaptureWindowThumbnailAsync(force: forceCapture || needsInitialFrame)
        refreshUIAsync()
    }

    func pushLiveFrame(
        surface: String,
        target: String,
        op: String,
        cursorX: Int?,
        cursorY: Int?,
        cursorLabel: String?,
        winW: Int?,
        winH: Int?,
        previewPath: String?,
        isHeadless: Bool? = nil
    ) {
        if checkStopped() { return }
        statusLock.lock()
        if !_isStopped && (_activeCount > 0 || _lastActiveTime > 0) {
            _lastActiveTime = CFAbsoluteTimeGetCurrent()
        }
        if !surface.isEmpty && _surface == "idle" {
            _surface = surface
        }
        if !target.isEmpty {
            _target = target
        }
        if !op.isEmpty {
            _op = op
        }
        if let hl = isHeadless {
            _isHeadlessWeb = hl
        }
        if let cx = cursorX, let cy = cursorY {
            _cursorPt = CGPoint(x: cx, y: cy)
        }
        if let cl = cursorLabel {
            _cursorLabel = cl
        }
        if let w = winW, let h = winH, w > 100, h > 80 {
            _previewCoordRect = CGRect(x: 0, y: 0, width: w, height: h)
        }
        _previewGeneration &+= 1
        statusLock.unlock()

        updateWebWindowPreview(
            target: target,
            op: op,
            cursorX: cursorX,
            cursorY: cursorY,
            cursorLabel: cursorLabel,
            winW: winW,
            winH: winH,
            previewPath: previewPath,
            previewNodes: nil,
            isHeadless: isHeadless
        )
    }

    func updateWebWindowPreview(
        target: String,
        op: String,
        cursorX: Int?,
        cursorY: Int?,
        cursorLabel: String?,
        winW: Int?,
        winH: Int?,
        previewPath: String?,
        previewNodes: [PreviewNodeSpec]? = nil,
        isHeadless: Bool? = nil
    ) {
        statusLock.lock()
        if let hl = isHeadless {
            _isHeadlessWeb = hl
        }
        if let cx = cursorX, let cy = cursorY {
            _cursorPt = CGPoint(x: cx, y: cy)
        }
        if let cl = cursorLabel {
            _cursorLabel = cl
        }
        if let w = winW, let h = winH, w > 100, h > 80 {
            _previewCoordRect = CGRect(x: 0, y: 0, width: w, height: h)
        }
        _previewGeneration &+= 1
        statusLock.unlock()

        if let path = previewPath, !path.isEmpty, FileManager.default.fileExists(atPath: path) {
            previewQueue.async { [weak self] in
                guard let self = self else { return }
                let url = URL(fileURLWithPath: path) as CFURL
                if let src = CGImageSourceCreateWithURL(url, nil),
                   let img = CGImageSourceCreateImageAtIndex(src, 0, nil) {
                    self.statusLock.lock()
                    self._previewImage = img
                    if self._previewCoordRect.width <= 100 {
                        self._previewCoordRect = CGRect(x: 0, y: 0, width: img.width, height: img.height)
                    }
                    self._previewGeneration &+= 1
                    self.statusLock.unlock()
                    self.refreshUIAsync()
                }
            }
        }
    }

    private func maybeCaptureWindowThumbnailAsync(force: Bool = false) {
        if ProcessInfo.processInfo.environment["FMU_PREVIEW"] == "0" { return }
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        if _isStopped || _surface == "web" || _surface == "browser" || _surface == "parallel" || _surface == "idle" {
            statusLock.unlock()
            return
        }
        if _previewCaptureInFlight {
            if force {
                _previewForcePending = true
            }
            statusLock.unlock()
            return
        }
        if !force && (now - _lastCaptureTime) < 0.16 {
            statusLock.unlock()
            return
        }
        _previewCaptureInFlight = true
        _previewForcePending = false
        _lastCaptureTime = now
        let pid = _targetPid
        let wid = _targetWindowID
        let targetHint = _target
        let surface = _surface
        let zoomed = _zoomedPreview
        statusLock.unlock()

        previewQueue.async { [weak self] in
            guard let self = self else { return }
            var shouldRunFollowup = false
            defer {
                self.statusLock.lock()
                self._previewCaptureInFlight = false
                if self._previewForcePending {
                    self._previewForcePending = false
                    shouldRunFollowup = true
                }
                self.statusLock.unlock()
                if shouldRunFollowup {
                    self.maybeCaptureWindowThumbnailAsync(force: true)
                }
            }
            var captured: CGImage?
            var resolvedRect: CGRect?
            var resolvedWid: CGWindowID = wid
            if let qMatch = self.resolveQuartzWindowBounds(pid: pid, windowID: wid, targetHint: targetHint, surface: surface) {
                resolvedWid = qMatch.wid
                resolvedRect = qMatch.rect
            }
            if #available(macOS 14.0, *), CGPreflightScreenCaptureAccess() {
                let sem = DispatchSemaphore(value: 0)
                let lowerHint = targetHint.lowercased()
                Task {
                    defer { sem.signal() }
                    guard let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false) else { return }
                    var targetWin = content.windows.first(where: { resolvedWid != 0 && $0.windowID == resolvedWid && $0.frame.width > 80 && $0.frame.height > 60 })
                    if targetWin == nil && pid > 0 {
                        let pidWins = content.windows.filter { $0.owningApplication?.processID == pid && $0.frame.width > 80 && $0.frame.height > 60 }
                        targetWin = pidWins.max(by: { a, b in
                            let aTitle = (a.title ?? "").lowercased()
                            let bTitle = (b.title ?? "").lowercased()
                            let aBonus = (!lowerHint.isEmpty && aTitle.contains(lowerHint)) ? 5_000_000.0 : 0.0
                            let bBonus = (!lowerHint.isEmpty && bTitle.contains(lowerHint)) ? 5_000_000.0 : 0.0
                            return (Double(a.frame.width * a.frame.height) + aBonus) < (Double(b.frame.width * b.frame.height) + bBonus)
                        })
                    }
                    guard let win = targetWin else { return }
                    resolvedRect = win.frame
                    resolvedWid = win.windowID
                    let filter = SCContentFilter(desktopIndependentWindow: win)
                    let cfg = SCStreamConfiguration()
                    let nativeScale = max(1.0, Double(filter.pointPixelScale))
                    let maxPixelW = zoomed ? 1160.0 : 720.0
                    let capScale = min(nativeScale, maxPixelW / max(1.0, Double(win.frame.width)))
                    cfg.width = max(80, Int((Double(win.frame.width) * capScale).rounded()))
                    cfg.height = max(60, Int((Double(win.frame.height) * capScale).rounded()))
                    cfg.scalesToFit = true
                    cfg.ignoreShadowsSingleWindow = true
                    cfg.showsCursor = false
                    captured = try? await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                }
                _ = sem.wait(timeout: .now() + 0.8)
            }
            if captured != nil || resolvedRect != nil {
                self.statusLock.lock()
                if self._surface == surface {
                    if let img = captured {
                        self._previewImage = img
                    }
                    if let r = resolvedRect, r.width > 80 {
                        self._targetWinRect = r
                        if surface == "app" {
                            self._previewCoordRect = r
                        }
                    }
                    if resolvedWid != 0 {
                        self._targetWindowID = resolvedWid
                    }
                    self._previewGeneration &+= 1
                }
                self.statusLock.unlock()
                self.refreshUIAsync()
            }
        }
    }

    func checkStopped() -> Bool {
        let fileExists = FileManager.default.fileExists(atPath: stopFilePath)
        statusLock.lock()
        let prev = _isStopped
        if fileExists != prev {
            _isStopped = fileExists
            if fileExists {
                _activeCount = 0
                _highlightUntil = 0
                _hudDismissedWhileStopped = false
                if _stopReason.isEmpty {
                    _stopReason = "User stopped fmu"
                }
            } else {
                _stopReason = ""
                _hudDismissedWhileStopped = false
            }
            _previewGeneration &+= 1
            statusLock.unlock()
            refreshUIAsync()
            return fileExists
        }
        statusLock.unlock()
        return fileExists
    }

    func triggerStop(reason: String = "User clicked Stop in macOS menu bar") {
        let payload = "{\"stopped\":true,\"ts\":\(Date().timeIntervalSince1970),\"reason\":\"\(reason)\"}\n"
        try? payload.write(toFile: stopFilePath, atomically: true, encoding: .utf8)
        statusLock.lock()
        _isStopped = true
        _activeCount = 0
        _highlightUntil = 0
        _hudDismissedWhileStopped = false
        _hudDismissedWhileHolding = false
        _justResumedUntil = 0
        _stopReason = reason
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    func triggerResume() {
        unlink(stopFilePath)
        statusLock.lock()
        let wasStopped = _isStopped
        _isStopped = false
        _stopReason = ""
        _activeCount = 0
        _lastActiveTime = 0
        _highlightUntil = 0
        _hudDismissedWhileStopped = false
        _hudDismissedWhileHolding = false
        if wasStopped {
            _justResumedUntil = CFAbsoluteTimeGetCurrent() + 1.2
        }
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync(immediateIfMain: true)
    }

    func performPrimaryClick() {
        if checkStopped() {
            triggerResume()
        } else {
            triggerStop(reason: "User clicked Stop in macOS menu bar")
        }
    }

    func beginWork(
        surface: String,
        target: String,
        op: String,
        stepCount: Int,
        agent: String,
        cursorX: Int? = nil,
        cursorY: Int? = nil,
        cursorLabel: String? = nil,
        winW: Int? = nil,
        winH: Int? = nil,
        previewPath: String? = nil,
        previewNodes: [PreviewNodeSpec]? = nil,
        isHeadless: Bool? = nil
    ) {
        if checkStopped() { return }
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        if _activeCount == 0 {
            _workStartTime = now
        }
        _activeCount += 1
        _lastActiveTime = now
        _justResumedUntil = 0
        _hudDismissedWhileHolding = false
        if stepCount > 0 {
            _lastStepCount = stepCount
        }
        let newSurf = surface.isEmpty ? "app" : surface
        if newSurf != _surface && newSurf != "parallel" && _surface != "parallel" {
            _previewImage = nil
            _cursorPt = nil
            _cursorLabel = ""
            if newSurf == "web" {
                _targetPid = 0
                _targetWindowID = 0
            }
        } else if !target.isEmpty && target != _target {
            if newSurf == "app" {
                _previewImage = nil
                _cursorPt = nil
                _cursorLabel = ""
            } else if newSurf == "web" && cursorX == nil && cursorY == nil {
                _cursorPt = nil
                _cursorLabel = ""
            }
        }
        _surface = newSurf
        if let hl = isHeadless { _isHeadlessWeb = hl }
        if !target.isEmpty { _target = target }
        if !op.isEmpty { _op = op }
        if !agent.isEmpty { _agent = agent }
        if let cx = cursorX, let cy = cursorY {
            _cursorPt = CGPoint(x: cx, y: cy)
        }
        if let cl = cursorLabel {
            _cursorLabel = cl
        }
        if let w = winW, let h = winH, w > 100, h > 80 {
            _previewCoordRect = CGRect(x: 0, y: 0, width: w, height: h)
        }
        _previewGeneration &+= 1
        statusLock.unlock()
        if newSurf == "web" {
            updateWebWindowPreview(
                target: target,
                op: op,
                cursorX: cursorX,
                cursorY: cursorY,
                cursorLabel: cursorLabel,
                winW: winW,
                winH: winH,
                previewPath: previewPath,
                previewNodes: previewNodes,
                isHeadless: isHeadless
            )
        }
        refreshUIAsync()
    }

    func updateWorkOp(_ op: String) {
        if op.isEmpty || checkStopped() { return }
        statusLock.lock()
        _lastActiveTime = CFAbsoluteTimeGetCurrent()
        _op = op
        _previewGeneration &+= 1
        statusLock.unlock()
        refreshUIAsync()
    }

    func endWork(
        surface: String,
        target: String,
        op: String,
        stepCount: Int? = nil,
        forceIdle: Bool = false,
        cursorX: Int? = nil,
        cursorY: Int? = nil,
        cursorLabel: String? = nil,
        winW: Int? = nil,
        winH: Int? = nil,
        previewPath: String? = nil,
        previewNodes: [PreviewNodeSpec]? = nil,
        isHeadless: Bool? = nil
    ) {
        statusLock.lock()
        _activeCount = max(0, _activeCount - 1)
        if let sc = stepCount, sc > 0 {
            _lastStepCount = sc
        }
        if forceIdle {
            _activeCount = 0
            _lastActiveTime = 0
            _hudDismissedWhileHolding = false
        } else {
            _lastActiveTime = CFAbsoluteTimeGetCurrent()
            if !surface.isEmpty && !(surface == "parallel" && (_surface == "web" || _surface == "app")) {
                _surface = surface
            }
        }
        if let hl = isHeadless { _isHeadlessWeb = hl }
        if !target.isEmpty && !(surface == "parallel" && !_target.isEmpty) { _target = target }
        if !op.isEmpty { _op = op }
        if let cx = cursorX, let cy = cursorY {
            _cursorPt = CGPoint(x: cx, y: cy)
        }
        if let cl = cursorLabel {
            _cursorLabel = cl
        }
        if let w = winW, let h = winH, w > 100, h > 80 {
            _previewCoordRect = CGRect(x: 0, y: 0, width: w, height: h)
        }
        _previewGeneration &+= 1
        statusLock.unlock()
        if !forceIdle && surface == "web" && (previewPath != nil || cursorX != nil) {
            updateWebWindowPreview(
                target: target,
                op: op,
                cursorX: cursorX,
                cursorY: cursorY,
                cursorLabel: cursorLabel,
                winW: winW,
                winH: winH,
                previewPath: previewPath,
                previewNodes: previewNodes,
                isHeadless: isHeadless
            )
        }
        refreshUIAsync()
    }

    func statusLine() -> String {
        _ = checkStopped()
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        let info = computeDisplayLocked(now: now)
        let visible = (statusItem != nil)
        let hudVis = (info.state == "working" && !info.hudDismissed) || (info.state == "stopped" && !info.hudDismissed) || (info.state == "idle" && info.justResumed)
        let prevVis = hudVis && info.showPreview
        let hlVis = info.highlightActive
        let hasFrame = (_previewImage != nil)
        let zoomed = _zoomedPreview
        statusLock.unlock()
        let escTarget = info.target.replacingOccurrences(of: "\"", with: "'")
        let escTitle = info.plainTitle.replacingOccurrences(of: "\"", with: "'")
        return "# fmu-menubar state=\"\(info.state)\" title=\"\(escTitle)\" stopped=\(info.stopped ? "true" : "false") surface=\"\(info.surface)\" target=\"\(escTarget)\" op=\"\(info.op)\" agent=\"\(info.agent)\" visible=\(visible ? "true" : "false") hud=\(hudVis ? "true" : "false") preview=\(prevVis ? "true" : "false") hasFrame=\(hasFrame ? "true" : "false") zoomed=\(zoomed ? "true" : "false") highlight=\(hlVis ? "true" : "false")"
    }

    private func refreshUIAsync(immediateIfMain: Bool = false) {
        if immediateIfMain && Thread.isMainThread {
            applyTitleOnMain(force: false)
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.applyTitleOnMain(force: false)
            }
        }
    }

    private func tickTimerOnMain() {
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        if _activeCount > 0 && _lastActiveTime > 0 && (now - _lastActiveTime) > maxInFlightSeconds {
            _activeCount = 0
            _lastActiveTime = 0
        }
        let isIdleQuiet = (
            _activeCount == 0
            && !_isStopped
            && _highlightUntil <= now
            && _justResumedUntil <= now
            && (_lastActiveTime == 0 || (now - _lastActiveTime) >= activeHoldSeconds + 0.2)
        )
        statusLock.unlock()
        if isIdleQuiet {
            _idleTicks = (_idleTicks + 1) % 7
            if _idleTicks != 0 {
                return
            }
        } else {
            _idleTicks = 0
        }
        _ = checkStopped()
        statusLock.lock()
        let info = computeDisplayLocked(now: now)
        if info.state == "working" || info.highlightActive {
            _spinnerPhase = (_spinnerPhase + 1) % 8
        }
        statusLock.unlock()
        if info.state == "working" {
            maybeCaptureWindowThumbnailAsync()
        }
        applyTitleOnMain(force: info.state == "working" || info.highlightActive)
    }

    private func renderMenuBarImage(
        state: String,
        shortTarget: String,
        spinnerPhase: Int,
        appearance: NSAppearance?
    ) -> NSImage {
        let statusLabel: String
        let ctaLabel: String
        let ctaBgColor: NSColor
        let capsuleFill: NSColor
        let capsuleBorder: NSColor

        let primaryTextColor = NSColor.white

        if state == "stopped" {
            statusLabel = "fmu Stopped"
            ctaLabel = "▶ Resume"
            ctaBgColor = NSColor(calibratedRed: 0.16, green: 0.68, blue: 0.29, alpha: 1.0)
            capsuleFill = NSColor(calibratedRed: 0.26, green: 0.08, blue: 0.08, alpha: 0.94)
            capsuleBorder = NSColor(calibratedRed: 0.98, green: 0.30, blue: 0.26, alpha: 0.90)
        } else if state == "working" {
            let cleanTgt = truncateLabel(shortTarget, maxChars: 12)
            statusLabel = cleanTgt.isEmpty ? "fmu Working" : "fmu · \(cleanTgt)"
            ctaLabel = "⏹ Stop"
            ctaBgColor = NSColor(calibratedRed: 0.94, green: 0.20, blue: 0.18, alpha: 1.0)
            capsuleFill = NSColor(calibratedRed: 0.06, green: 0.20, blue: 0.11, alpha: 0.94)
            capsuleBorder = NSColor(calibratedRed: 0.22, green: 0.88, blue: 0.40, alpha: 0.92)
        } else {
            statusLabel = "fmu Idle"
            ctaLabel = ""
            ctaBgColor = .clear
            capsuleFill = NSColor(calibratedRed: 0.11, green: 0.13, blue: 0.16, alpha: 0.90)
            capsuleBorder = NSColor(calibratedWhite: 0.45, alpha: 0.65)
        }

        let statusFont = NSFont.systemFont(ofSize: 11.5, weight: state == "idle" ? .medium : .semibold)
        let statusAttrs: [NSAttributedString.Key: Any] = [
            .font: statusFont,
            .foregroundColor: state == "idle" ? primaryTextColor.withAlphaComponent(0.85) : primaryTextColor
        ]
        let statusAttrStr = NSAttributedString(string: statusLabel, attributes: statusAttrs)
        let statusSz = statusAttrStr.size()

        let hasCta = !ctaLabel.isEmpty
        let ctaFont = NSFont.systemFont(ofSize: 10.5, weight: .bold)
        let ctaAttrs: [NSAttributedString.Key: Any] = [
            .font: ctaFont,
            .foregroundColor: NSColor.white
        ]
        let ctaAttrStr = NSAttributedString(string: ctaLabel, attributes: ctaAttrs)
        let ctaSz = ctaAttrStr.size()

        let leftIconSpace: CGFloat = 20
        let ctaPadX: CGFloat = 10
        let ctaW = hasCta ? (ceil(ctaSz.width) + ctaPadX) : 0
        let ctaH: CGFloat = 15
        let totalW = hasCta
            ? (leftIconSpace + ceil(statusSz.width) + 7 + ctaW + 5)
            : (leftIconSpace + ceil(statusSz.width) + 8)
        let totalH: CGFloat = 22

        let img = NSImage(size: NSSize(width: totalW, height: totalH), flipped: false) { rect in
            let outerRect = rect.insetBy(dx: 1.0, dy: 2.0)
            let outerPath = NSBezierPath(roundedRect: outerRect, xRadius: 6.0, yRadius: 6.0)
            capsuleFill.setFill()
            outerPath.fill()
            outerPath.lineWidth = 1.0
            capsuleBorder.setStroke()
            outerPath.stroke()

            let midY = rect.midY
            let dotCenter = CGPoint(x: 11.0, y: midY)

            if state == "stopped" {
                let circle = NSBezierPath(ovalIn: NSRect(x: dotCenter.x - 4.8, y: dotCenter.y - 4.8, width: 9.6, height: 9.6))
                NSColor(calibratedRed: 1.0, green: 0.23, blue: 0.19, alpha: 1.0).setFill()
                circle.fill()
                let sq = NSBezierPath(rect: NSRect(x: dotCenter.x - 2.0, y: dotCenter.y - 2.0, width: 4.0, height: 4.0))
                NSColor.white.setFill()
                sq.fill()
            } else if state == "working" {
                let ring = NSBezierPath(ovalIn: NSRect(x: dotCenter.x - 5.2, y: dotCenter.y - 5.2, width: 10.4, height: 10.4))
                ring.lineWidth = 1.4
                NSColor(calibratedRed: 0.19, green: 0.82, blue: 0.35, alpha: 0.30).setStroke()
                ring.stroke()

                let startDeg = CGFloat((spinnerPhase * 45) % 360)
                let arc = NSBezierPath()
                arc.appendArc(withCenter: dotCenter, radius: 5.2, startAngle: startDeg, endAngle: startDeg + 220.0, clockwise: false)
                arc.lineWidth = 1.8
                arc.lineCapStyle = .round
                NSColor(calibratedRed: 0.18, green: 0.88, blue: 0.38, alpha: 1.0).setStroke()
                arc.stroke()

                let centerDot = NSBezierPath(ovalIn: NSRect(x: dotCenter.x - 2.4, y: dotCenter.y - 2.4, width: 4.8, height: 4.8))
                NSColor(calibratedRed: 0.18, green: 0.88, blue: 0.38, alpha: 1.0).setFill()
                centerDot.fill()
            } else {
                let idleDot = NSBezierPath(ovalIn: NSRect(x: dotCenter.x - 3.6, y: dotCenter.y - 3.6, width: 7.2, height: 7.2))
                primaryTextColor.withAlphaComponent(0.42).setFill()
                idleDot.fill()
            }

            // Draw status text
            statusAttrStr.draw(at: NSPoint(x: leftIconSpace, y: midY - statusSz.height / 2.0))

            // Draw CTA button pill inside right side of menu bar item only when working or stopped
            if hasCta {
                let ctaX = leftIconSpace + ceil(statusSz.width) + 6.0
                let ctaRect = NSRect(x: ctaX, y: midY - ctaH / 2.0, width: ctaW, height: ctaH)
                let ctaPath = NSBezierPath(roundedRect: ctaRect, xRadius: 4.5, yRadius: 4.5)
                ctaBgColor.setFill()
                ctaPath.fill()

                ctaAttrStr.draw(at: NSPoint(x: ctaRect.midX - ctaSz.width / 2.0, y: ctaRect.midY - ctaSz.height / 2.0))
            }
            return true
        }
        img.isTemplate = false
        return img
    }

    private func positionAndUpdateHudOnMain(
        info: (state: String, plainTitle: String, surface: String, target: String, op: String, agent: String, stopped: Bool, isLiveInFlight: Bool, justResumed: Bool, hudDismissed: Bool, showPreview: Bool, highlightActive: Bool),
        spinnerPhase: Int
    ) {
        guard let panel = hudPanel, let view = hudView else { return }
        if ProcessInfo.processInfo.environment["FMU_HUD"] == "0" {
            panel.orderOut(nil)
            highlightPanel?.orderOut(nil)
            return
        }

        statusLock.lock()
        let startTs = _workStartTime
        let prevImg = _previewImage
        let winR = _targetWinRect
        let coordR = _previewCoordRect
        let headlessWeb = _isHeadlessWeb
        let stepCnt = _lastStepCount
        let curPt = _cursorPt
        let curLbl = _cursorLabel
        let zoomed = _zoomedPreview
        let customOrigin = _customHudOrigin
        statusLock.unlock()

        // 1. Update on-screen Target Window Highlight frame if active (only when an on-screen window exists)
        if let hlPanel = highlightPanel, let hlView = highlightView {
            let canHighlightScreen = info.highlightActive && !info.stopped && !(info.surface == "web" && headlessWeb)
            if canHighlightScreen {
                let primaryH = NSScreen.screens.first?.frame.height ?? 900.0
                let qRect = (winR.width > 80 && winR.height > 60) ? winR : CGRect(x: 160, y: 120, width: 800, height: 560)
                let pad: CGFloat = 6.0
                let appKitRect = NSRect(
                    x: qRect.origin.x - pad,
                    y: primaryH - qRect.origin.y - qRect.height - pad,
                    width: qRect.width + pad * 2.0,
                    height: qRect.height + pad * 2.0
                )
                hlView.targetLabel = info.target
                hlView.opLabel = info.op
                hlView.cursorLabel = curLbl
                if let cpt = curPt {
                    if info.surface == "app" && cpt.x >= qRect.origin.x && cpt.x <= qRect.maxX && cpt.y >= qRect.origin.y && cpt.y <= qRect.maxY {
                        hlView.cursorLocalPt = CGPoint(x: cpt.x - qRect.origin.x + pad, y: cpt.y - qRect.origin.y + pad)
                    } else if coordR.width > 0 && coordR.height > 0 {
                        let nx = (cpt.x - coordR.origin.x) / coordR.width
                        let ny = (cpt.y - coordR.origin.y) / coordR.height
                        if nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1 {
                            hlView.cursorLocalPt = CGPoint(x: nx * qRect.width + pad, y: ny * qRect.height + pad)
                        } else {
                            hlView.cursorLocalPt = nil
                        }
                    } else {
                        hlView.cursorLocalPt = nil
                    }
                } else {
                    hlView.cursorLocalPt = nil
                }
                hlView.pulsePhase = spinnerPhase
                hlView.needsDisplay = true
                hlPanel.setFrame(appKitRect, display: true)
                if !hlPanel.isVisible {
                    hlPanel.orderFrontRegardless()
                }
            } else if hlPanel.isVisible {
                hlPanel.orderOut(nil)
            }
        }

        // 2. Update Top-Right Floating HUD + Live Pixel Monitor
        let shouldShowHud = (info.state == "working" && !info.hudDismissed)
            || (info.state == "stopped" && !info.hudDismissed)
            || (info.state == "idle" && info.justResumed)

        if !shouldShowHud {
            if panel.isVisible {
                panel.orderOut(nil)
            }
            return
        }

        let showPrev = (info.state == "working") && info.showPreview
        view.state = info.state
        view.isLiveInFlight = info.isLiveInFlight
        view.elapsedSec = (info.isLiveInFlight && startTs > 0) ? max(0.0, CFAbsoluteTimeGetCurrent() - startTs) : 0.0
        view.stepCount = stepCnt
        view.surface = info.surface
        view.target = info.target
        view.op = info.op
        view.spinnerPhase = spinnerPhase
        view.justResumed = info.justResumed
        view.showPreview = showPrev
        view.isZoomedPreview = zoomed
        view.highlightActive = info.highlightActive
        view.isHeadlessWeb = (info.surface == "web" && headlessWeb)
        view.previewImage = prevImg
        view.coordRect = coordR
        view.cursorPt = curPt
        view.cursorLabel = curLbl
        view.needsDisplay = true

        let screen = statusItem?.button?.window?.screen ?? NSScreen.main ?? NSScreen.screens.first
        if let scr = screen {
            let vis = scr.visibleFrame
            let hudW: CGFloat
            let hudH: CGFloat
            if !showPrev {
                hudW = 388
                hudH = 44
            } else {
                let rawAspect: CGFloat
                if let img = prevImg, img.height > 0 {
                    rawAspect = CGFloat(img.width) / CGFloat(img.height)
                } else if coordR.height > 0 && coordR.width > 0 {
                    rawAspect = coordR.width / coordR.height
                } else {
                    rawAspect = 16.0 / 10.0
                }
                let aspect = min(2.35, max(0.72, rawAspect))
                let targetW: CGFloat = zoomed ? 740 : 460
                let clampedW = min(vis.width - 20, targetW)
                let idealStageH = (clampedW - 16.0) / aspect
                let minStageH: CGFloat = zoomed ? 280 : 188
                let maxStageH: CGFloat = zoomed ? 470 : 284
                let stageH = min(maxStageH, max(minStageH, idealStageH))
                hudW = round(clampedW)
                hudH = min(vis.height - 20, round(stageH + 76.0))
            }

            let x: CGFloat
            let y: CGFloat
            if let custom = customOrigin {
                x = min(vis.maxX - hudW - 6, max(vis.minX + 6, custom.x))
                y = min(vis.maxY - hudH - 6, max(vis.minY + 6, custom.y))
            } else {
                var anchorX = vis.maxX - hudW - 14
                if let btnWin = statusItem?.button?.window {
                    let btnFrame = btnWin.frame
                    anchorX = min(vis.maxX - hudW - 10, max(vis.minX + 10, btnFrame.maxX - hudW))
                }
                x = anchorX
                y = vis.maxY - hudH - 6
            }
            panel.setFrame(NSRect(x: x, y: y, width: hudW, height: hudH), display: true)
        }
        if !panel.isVisible {
            panel.orderFrontRegardless()
        }
    }

    private func applyTitleOnMain(force: Bool) {
        guard let btn = statusItem?.button else { return }
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        let info = computeDisplayLocked(now: now)
        let phase = _spinnerPhase
        let gen = _previewGeneration
        let zoomed = _zoomedPreview
        let sig = "\(info.state)|\(info.plainTitle)|\(info.isLiveInFlight)|\(info.justResumed)|\(info.hudDismissed)|\(info.showPreview)|\(info.highlightActive)|\(zoomed)|\(gen)|\(info.state == "working" || info.highlightActive ? phase : 0)"
        if !force && sig == _lastRenderedSignature {
            statusLock.unlock()
            return
        }
        _lastRenderedSignature = sig
        statusLock.unlock()

        let shortTarget = info.target.isEmpty ? (info.surface == "web" ? "Chrome" : "macOS") : info.target
        let img = renderMenuBarImage(
            state: info.state,
            shortTarget: shortTarget,
            spinnerPhase: phase,
            appearance: btn.effectiveAppearance
        )
        btn.title = ""
        btn.attributedTitle = NSAttributedString(string: "")
        btn.image = img
        btn.imagePosition = .imageOnly
        btn.setAccessibilityTitle(info.plainTitle)

        if info.state == "stopped" {
            btn.toolTip = "Faster Mac Use (fmu) is STOPPED — Click once to Resume agent automation (Right-click for menu)"
        } else if info.state == "working" {
            btn.toolTip = "Faster Mac Use (fmu) is WORKING on \(shortTarget) (\(info.op)) — Click once to STOP agent immediately (Right-click for menu)"
        } else {
            btn.toolTip = "Faster Mac Use (fmu) is IDLE (not working) — Click for menu"
        }

        positionAndUpdateHudOnMain(info: info, spinnerPhase: phase)
    }

    @objc func handleStatusBarClick(_ sender: Any?) {
        let ev = NSApp.currentEvent
        let isRightClick = (ev?.type == .rightMouseUp)
            || (ev?.modifierFlags.contains(.control) == true)
            || (ev?.modifierFlags.contains(.option) == true)
        if isRightClick {
            showContextMenu()
            return
        }
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        let info = computeDisplayLocked(now: now)
        statusLock.unlock()
        if info.state == "idle" {
            showContextMenu()
            return
        }
        performPrimaryClick()
    }

    func showContextMenuFromHud(event: NSEvent, in view: NSView) {
        let menu = buildContextMenu()
        NSMenu.popUpContextMenu(menu, with: event, for: view)
    }

    private func buildContextMenu() -> NSMenu {
        _ = checkStopped()
        let now = CFAbsoluteTimeGetCurrent()
        statusLock.lock()
        let info = computeDisplayLocked(now: now)
        let prevOn = _showLivePreview
        let zoomed = _zoomedPreview
        let hasCustomPos = (_customHudOrigin != nil)
        statusLock.unlock()

        let menu = NSMenu()
        let headerItem = NSMenuItem(title: "Faster Mac Use (fmu)", action: nil, keyEquivalent: "")
        headerItem.isEnabled = false
        menu.addItem(headerItem)

        let stateDesc: String
        if info.stopped {
            stateDesc = "Status: 🛑 Stopped by User (Agent calls blocked)"
        } else if info.state == "working" {
            stateDesc = "Status: 🟢 Working — \(info.surface) (\(info.target.isEmpty ? "active" : info.target) · \(info.op.isEmpty ? "work" : info.op))"
        } else {
            stateDesc = "Status: ⚪ Idle (Agent is not working right now)"
        }
        let statusMenuItem = NSMenuItem(title: stateDesc, action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)

        if !info.target.isEmpty {
            let lastItem = NSMenuItem(title: "Last Target: \(info.target) (\(info.op.isEmpty ? "snap" : info.op))", action: nil, keyEquivalent: "")
            lastItem.isEnabled = false
            menu.addItem(lastItem)
        }

        menu.addItem(NSMenuItem.separator())

        if info.stopped {
            let resumeItem = NSMenuItem(title: "▶ Resume Agent Automation", action: #selector(menuResumeClicked), keyEquivalent: "")
            resumeItem.target = self
            menu.addItem(resumeItem)
        } else if info.state == "working" {
            let stopItem = NSMenuItem(title: "⏹ Stop Agent Immediately", action: #selector(menuStopClicked), keyEquivalent: "")
            stopItem.target = self
            menu.addItem(stopItem)
        }

        let hlItem = NSMenuItem(title: "🎯 Highlight Target Window on Screen", action: #selector(menuHighlightClicked), keyEquivalent: "")
        hlItem.target = self
        menu.addItem(hlItem)

        let prevTitle = prevOn ? "📺 Hide Live Screen Monitor" : "📺 Show Live Screen Monitor"
        let prevItem = NSMenuItem(title: prevTitle, action: #selector(menuTogglePreviewClicked), keyEquivalent: "")
        prevItem.target = self
        menu.addItem(prevItem)

        let zoomTitle = zoomed ? "🗗 Compact Live Screen Monitor" : "🔍 Large HD Live Screen Monitor"
        let zoomItem = NSMenuItem(title: zoomTitle, action: #selector(menuToggleZoomClicked), keyEquivalent: "")
        zoomItem.target = self
        menu.addItem(zoomItem)

        if hasCustomPos {
            let posItem = NSMenuItem(title: "⌖ Dock Live Monitor to Top-Right", action: #selector(menuResetPositionClicked), keyEquivalent: "")
            posItem.target = self
            menu.addItem(posItem)
        }

        let resetItem = NSMenuItem(title: "↺ Reset Agent State", action: #selector(menuResetClicked), keyEquivalent: "")
        resetItem.target = self
        menu.addItem(resetItem)

        menu.addItem(NSMenuItem.separator())

        let quitItem = NSMenuItem(title: "✕ Quit fmu Menu Bar & Daemon", action: #selector(menuQuitClicked), keyEquivalent: "")
        quitItem.target = self
        menu.addItem(quitItem)
        return menu
    }

    private func showContextMenu() {
        guard let item = statusItem else { return }
        let menu = buildContextMenu()
        item.menu = menu
        item.button?.performClick(nil)
        item.menu = nil
    }

    @objc private func menuStopClicked() {
        triggerStop(reason: "User clicked Stop in macOS menu bar")
    }

    @objc private func menuResumeClicked() {
        triggerResume()
    }

    @objc private func menuHighlightClicked() {
        triggerHighlightWindow()
    }

    @objc private func menuTogglePreviewClicked() {
        toggleLivePreview()
    }

    @objc private func menuToggleZoomClicked() {
        toggleZoomPreview()
    }

    @objc private func menuResetPositionClicked() {
        resetHudPosition()
    }

    @objc private func menuResetClicked() {
        triggerResume()
        let rtSock = self.runtimeSocketPath
        DispatchQueue.global(qos: .userInteractive).async {
            EngineState.shared.lock.lock()
            EngineState.shared.reset()
            EngineState.shared.lock.unlock()
            _ = trySendToDaemon(socketPath: rtSock, payload: "{\"reset\":true,\"resume\":true}")
        }
    }

    @objc private func menuQuitClicked() {
        let rtSock = self.runtimeSocketPath
        let axSock = self.daemonSocketPath
        let stopFile = self.stopFilePath
        self.hudPanel?.orderOut(nil)
        self.highlightPanel?.orderOut(nil)
        if let item = self.statusItem {
            NSStatusBar.system.removeStatusItem(item)
            self.statusItem = nil
        }
        DispatchQueue.global(qos: .userInteractive).async {
            _ = trySendToDaemon(socketPath: rtSock, payload: "{\"shutdown_daemon\":true}")
            unlink(axSock)
            unlink(stopFile)
            exit(0)
        }
    }
    func dumpUiSnapshots(toDir dirPath: String) -> String {
        if !Thread.isMainThread {
            let waitDeadline = CFAbsoluteTimeGetCurrent() + 0.35
            while CFAbsoluteTimeGetCurrent() < waitDeadline {
                statusLock.lock()
                let busy = _previewCaptureInFlight
                let needsAppFrame = (_surface == "app" && _previewImage == nil)
                statusLock.unlock()
                if !busy && !needsAppFrame {
                    break
                }
                if needsAppFrame && !busy {
                    maybeCaptureWindowThumbnailAsync(force: true)
                }
                Thread.sleep(forTimeInterval: 0.015)
            }
            previewQueue.sync {}
        }
        var saved: [String] = []
        let sem = DispatchSemaphore(value: 0)
        DispatchQueue.main.async { [weak self] in
            defer { sem.signal() }
            guard let self = self else { return }
            self.applyTitleOnMain(force: true)
            try? FileManager.default.createDirectory(atPath: dirPath, withIntermediateDirectories: true)

            func saveViewPng(_ v: NSView, name: String) {
                guard v.bounds.width > 10, v.bounds.height > 10,
                      let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { return }
                v.cacheDisplay(in: v.bounds, to: rep)
                if let png = rep.representation(using: .png, properties: [:]) {
                    let p = (dirPath as NSString).appendingPathComponent(name)
                    try? png.write(to: URL(fileURLWithPath: p))
                    saved.append(p)
                }
            }

            if let hv = self.hudView {
                saveViewPng(hv, name: "hud-\(hv.state).png")
            }
            if let hlv = self.highlightView {
                if hlv.bounds.width < 100 {
                    hlv.frame = NSRect(x: 0, y: 0, width: 244, height: 332)
                }
                saveViewPng(hlv, name: "highlight.png")
            }
            if let btn = self.statusItem?.button {
                saveViewPng(btn, name: "menubar-\(self.hudView?.state ?? "status").png")
            }
        }
        _ = sem.wait(timeout: .now() + 2.0)
        return "# fmu-ui-snapshots count=\(saved.count) files=\(saved.joined(separator: ","))"
    }
}

func interruptibleSleep(ms: Int) -> Bool {
    let totalSec = max(0.0, Double(ms) / 1000.0)
    let deadline = DispatchTime.now().uptimeNanoseconds + UInt64(totalSec * 1_000_000_000.0)
    while DispatchTime.now().uptimeNanoseconds < deadline {
        if MenuBarController.shared.checkStopped() {
            return false
        }
        let now = DispatchTime.now().uptimeNanoseconds
        if now >= deadline { break }
        let remSec = Double(deadline - now) / 1_000_000_000.0
        let sliceSec = min(0.015, remSec)
        if sliceSec > 0 {
            Thread.sleep(forTimeInterval: sliceSec)
        }
    }
    return !MenuBarController.shared.checkStopped()
}

func settleAndWalkAXTree(
    root: AXUIElement,
    winKey: String,
    pid: pid_t,
    cachedNodes: [NodeRecord],
    cachedElements: [AXUIElement],
    prevCrc: String,
    query: String?,
    shouldSettle: Bool
) -> ([NodeRecord], [AXUIElement], String) {
    let state = EngineState.shared
    if shouldSettle {
        _ = interruptibleSleep(ms: 10)
    }
    var (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
    var curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
    if shouldSettle {
        for _ in 0..<2 where curCrc == prevCrc {
            if !interruptibleSleep(ms: 15) { break }
            (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
            curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
        }
    }
    _ = state.consumeDirtyState(pid)
    state.windowSyncedSeq[winKey] = state.currentPidSeq(pid)
    return (nodes, els, curCrc)
}

func handleControlRequest(_ req: BatchRequest) -> String? {
    if let upd = req.statusUpdate {
        let st = (upd.state ?? "").lowercased()
        if st == "working" {
            MenuBarController.shared.beginWork(
                surface: upd.surface ?? "web",
                target: upd.target ?? "Chrome",
                op: upd.op ?? "work",
                stepCount: upd.stepCount ?? 1,
                agent: upd.agent ?? req.effectiveAgent,
                cursorX: upd.cursorX,
                cursorY: upd.cursorY,
                cursorLabel: upd.cursorLabel,
                winW: upd.winW,
                winH: upd.winH,
                previewPath: upd.previewPath,
                previewNodes: upd.previewNodes,
                isHeadless: upd.isHeadless
            )
        } else if st == "frame" {
            MenuBarController.shared.pushLiveFrame(
                surface: upd.surface ?? "web",
                target: upd.target ?? "",
                op: upd.op ?? "",
                cursorX: upd.cursorX,
                cursorY: upd.cursorY,
                cursorLabel: upd.cursorLabel,
                winW: upd.winW,
                winH: upd.winH,
                previewPath: upd.previewPath,
                isHeadless: upd.isHeadless
            )
        } else if st == "done" || st == "idle" {
            MenuBarController.shared.endWork(
                surface: upd.surface ?? "web",
                target: upd.target ?? "",
                op: upd.op ?? "",
                stepCount: upd.stepCount,
                forceIdle: st == "idle",
                cursorX: upd.cursorX,
                cursorY: upd.cursorY,
                cursorLabel: upd.cursorLabel,
                winW: upd.winW,
                winH: upd.winH,
                previewPath: upd.previewPath,
                previewNodes: upd.previewNodes,
                isHeadless: upd.isHeadless
            )
        }
        return MenuBarController.shared.statusLine()
    }

    if let snapDir = req.dumpUiSnapshots, !snapDir.isEmpty {
        return MenuBarController.shared.dumpUiSnapshots(toDir: snapDir)
    }

    if req.highlightWindow == true {
        MenuBarController.shared.triggerHighlightWindow()
        return MenuBarController.shared.statusLine()
    }

    if req.togglePreview == true {
        MenuBarController.shared.toggleLivePreview()
        return MenuBarController.shared.statusLine()
    }

    if req.toggleZoom == true {
        MenuBarController.shared.toggleZoomPreview()
        return MenuBarController.shared.statusLine()
    }

    if req.clickStatusBar == true {
        MenuBarController.shared.performPrimaryClick()
        return MenuBarController.shared.statusLine()
    }

    if req.stop == true {
        MenuBarController.shared.triggerStop(reason: "User stopped fmu")
        return MenuBarController.shared.statusLine()
    }

    if req.resume == true {
        MenuBarController.shared.triggerResume()
        if req.reset != true && (req.app ?? "").isEmpty {
            return MenuBarController.shared.statusLine()
        }
    }

    if req.statusBar == true {
        return MenuBarController.shared.statusLine()
    }

    if MenuBarController.shared.checkStopped() {
        return userStoppedErrorMessage
    }

    if let imgPath = req.imagePath, !imgPath.isEmpty {
        return performImageFileOCR(
            imagePath: imgPath,
            clipX: req.clipX ?? 0.0,
            clipY: req.clipY ?? 0.0,
            clipW: req.clipW ?? 0.0,
            clipH: req.clipH ?? 0.0
        )
    }
    return nil
}

func executeRequest(_ req: BatchRequest) -> String {
    let t0 = DispatchTime.now().uptimeNanoseconds
    if let ctrl = handleControlRequest(req) {
        return ctrl
    }

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
        MenuBarController.shared.endWork(surface: "idle", target: "", op: "", forceIdle: true)
        return "# fmu-ax state reset"
    }
    guard let appName = req.app, !appName.isEmpty else {
        return "Error: missing `app` parameter"
    }
    guard let app = findApp(nameOrBundle: appName) else {
        return "Error: app not found (\(appName))"
    }

    let agentId = req.effectiveAgent
    let firstOp = req.steps?.first?.effectiveOp ?? (req.diff == true ? "diff" : "snap")
    var lastExecutedOp = firstOp
    let totalSteps = req.steps?.count ?? 0
    MenuBarController.shared.beginWork(
        surface: "app",
        target: app.name,
        op: firstOp,
        stepCount: max(1, totalSteps),
        agent: agentId
    )
    defer {
        MenuBarController.shared.endWork(surface: "app", target: app.name, op: lastExecutedOp, stepCount: max(1, totalSteps))
    }
    let hasQuery = !(req.query ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    let root: AXUIElement
    let winTitle: String
    switch resolveTargetWindow(
        app,
        windowFilter: req.effectiveWindowFilter,
        windowIndex: req.effectiveWindowIndex,
        urlFilter: req.effectiveUrlFilter
    ) {
    case .notFound(let errMsg):
        return errMsg
    case .matched(let r, let wt):
        root = r
        winTitle = wt
    }

    let rootIsMain = isWindowMain(app.pid, root)
    let winKey = "\(app.name.lowercased()):\(winTitle)"
    let baselineKey = "\(agentId)|\(winKey)"
    let pidChanged = (state.appPidByKey[winKey] != nil && state.appPidByKey[winKey] != app.pid)
    if let oldPid = state.appPidByKey[winKey], pidChanged {
        state.invalidateAppWindows(forPid: oldPid)
        let winPrefix = "\(winKey)|"
        for (sKey, uid) in state.structKeyToUid where sKey.hasPrefix(winPrefix) {
            state.uidToElement.removeValue(forKey: uid)
            state.uidToRecord.removeValue(forKey: uid)
        }
        state.latestWindowNodes.removeValue(forKey: winKey)
        state.latestWindowElements.removeValue(forKey: winKey)
        state.windowSyncedSeq.removeValue(forKey: winKey)
    }
    state.appPidByKey[winKey] = app.pid
    let hadBaseline = state.windowBaselines[baselineKey] != nil
    let hasSteps = !(req.steps?.isEmpty ?? true)
    if hasSteps {
        let initAttrs = readBatchedAttrs(root)
        let initBounds = CGRect(
            x: initAttrs.x,
            y: initAttrs.y,
            width: max(200, initAttrs.w),
            height: max(140, initAttrs.h)
        )
        MenuBarController.shared.updateAppWindowPreview(
            pid: app.pid,
            windowID: app.windowID,
            appName: app.name,
            winTitle: winTitle,
            winRect: initBounds,
            nodes: [],
            cursor: state.agentCursors[agentId],
            forceCapture: true
        )
    }

    var currentNodes: [NodeRecord]
    var currentElements: [AXUIElement]
    if !pidChanged && !hasQuery && (hasSteps || req.diff != false),
       let cachedNodes = state.latestWindowNodes[winKey] ?? state.windowBaselines[baselineKey],
       !cachedNodes.isEmpty {
        let cachedEls = state.latestWindowElements[winKey] ?? cachedNodes.compactMap { state.uidToElement[$0.uid] }
        if cachedEls.count == cachedNodes.count {
            if hasSteps {
                currentNodes = cachedNodes
                currentElements = cachedEls
            } else {
                let curSeq = state.currentPidSeq(app.pid)
                let syncedSeq = state.windowSyncedSeq[winKey]
                if !state.isStructurallyDirty(app.pid) && syncedSeq == curSeq {
                    currentNodes = cachedNodes
                    currentElements = cachedEls
                } else {
                    let dirtyInfo = state.consumeDirtyState(app.pid)
                    if !dirtyInfo.structural,
                       let refreshed = refreshDirtyLeavesInPlace(
                           nodes: cachedNodes,
                           elements: cachedEls,
                           dirtyHashes: dirtyInfo.hashes,
                           refreshValueBearers: true
                       ) {
                        currentNodes = refreshed
                        currentElements = cachedEls
                        state.windowSyncedSeq[winKey] = dirtyInfo.seq
                    } else {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        _ = state.consumeDirtyState(app.pid)
                        state.windowSyncedSeq[winKey] = state.currentPidSeq(app.pid)
                    }
                }
            }
        } else {
            (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
            _ = state.consumeDirtyState(app.pid)
            state.windowSyncedSeq[winKey] = state.currentPidSeq(app.pid)
        }
    } else {
        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
        if !hasQuery {
            _ = state.consumeDirtyState(app.pid)
            state.windowSyncedSeq[winKey] = state.currentPidSeq(app.pid)
        }
    }
    if !hasQuery && !currentNodes.isEmpty {
        state.latestWindowNodes[winKey] = currentNodes
        state.latestWindowElements[winKey] = currentElements
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
            if MenuBarController.shared.checkStopped() {
                return userStoppedErrorMessage
            }
            let op = step.effectiveOp
            if op.isEmpty {
                stepErrors.append("step \(i+1): missing 'op' (or 'action') field in step; supported ops: click, set, move, type, key, menu, wait, settle, ocr")
                continue
            }
            lastExecutedOp = op
            MenuBarController.shared.updateWorkOp(op)
            defer {
                if op == "click" || op == "set" || op == "move" || op == "type" || op == "key" || op == "menu" {
                    MenuBarController.shared.updateAppCursorOnly(cursor: state.agentCursors[agentId], op: op)
                }
            }
            if op == "upload" {
                stepErrors.append("step \(i+1): op 'upload' is only supported on surface=\"web\" via the MV3 extension CDP bridge (DOM.setFileInputFiles)")
                continue
            }
            if op != "move" && op != "ocr" && op != "snapshot" && op != "diff" {
                didMutate = true
            }
            switch op {
            case "snapshot", "diff":
                break
            case "wait":
                if !interruptibleSleep(ms: step.ms ?? 30) {
                    return userStoppedErrorMessage
                }
            case "settle":
                if !interruptibleSleep(ms: 10) {
                    return userStoppedErrorMessage
                }
            case "ocr":
                ocrOutput.append(contentsOf: performWindowOCR(app: app, fallbackNodes: currentNodes))
            case "key":
                guard let chord = step.effectiveKey else {
                    if !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): key op requires 'key' (or 'name'/'value') parameter")
                    }
                    continue
                }
                let lc = chord.lowercased()
                var handledViaAX = false
                if lc == "escape" || lc == "esc" || lc == "clear" {
                    if let clrIdx = currentNodes.firstIndex(where: {
                        $0.role == "button" && ($0.label.lowercased() == "clear" || $0.label.lowercased() == "all clear")
                    }), clrIdx < currentElements.count {
                        if AXUIElementPerformAction(currentElements[clrIdx], kAXPressAction as CFString) == .success {
                            let cNode = currentNodes[clrIdx]
                            state.agentCursors[agentId] = VirtualCursorState(
                                agent: agentId,
                                app: app.name,
                                window: winTitle,
                                uid: cNode.uid,
                                label: cNode.label,
                                x: cNode.x + cNode.w / 2,
                                y: cNode.y + cNode.h / 2,
                                lastOp: "key"
                            )
                            handledViaAX = true
                        }
                    }
                    if !handledViaAX {
                        var cancelRef: CFTypeRef?
                        if AXUIElementCopyAttributeValue(root, kAXCancelButtonAttribute as CFString, &cancelRef) == .success,
                           let cancelBtn = cancelRef,
                           AXUIElementPerformAction(cancelBtn as! AXUIElement, kAXPressAction as CFString) == .success {
                            handledViaAX = true
                        } else if detectDisabledNSOpenPanel(nodes: currentNodes) != nil,
                                  let cancelIdx = currentNodes.firstIndex(where: {
                                      $0.role == "button" && ["cancel", "取消"].contains($0.label.lowercased())
                                  }), cancelIdx < currentElements.count {
                            if AXUIElementPerformAction(currentElements[cancelIdx], kAXPressAction as CFString) == .success {
                                handledViaAX = true
                            }
                        }
                    }
                } else if !rootIsMain && (lc == "return" || lc == "enter") {
                    var defRef: CFTypeRef?
                    if AXUIElementCopyAttributeValue(root, kAXDefaultButtonAttribute as CFString, &defRef) == .success,
                       let defBtn = defRef,
                       AXUIElementPerformAction(defBtn as! AXUIElement, kAXPressAction as CFString) == .success {
                        handledViaAX = true
                    }
                }
                if !handledViaAX {
                    if rootIsMain {
                        postKeyChordToPid(app.pid, chord: chord)
                        Thread.sleep(forTimeInterval: 0.012)
                    } else if !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): key '\(chord)' refused on non-main window \"\(winTitle)\" because CGEvent.postToPid routes keys to the app's AXMainWindow; use AX click/set or surface=\"web\" MV3 extension bridge")
                    }
                }
            case "type":
                guard let text = step.effectiveValue ?? step.effectiveName else {
                    if !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): type op requires 'value' (or 'text'/'name') parameter")
                    }
                    continue
                }
                if rootIsMain {
                    postBatchedUnicodeTextToPid(app.pid, text: text)
                } else {
                    var insertedViaAX = false
                    if let cur = state.agentCursors[agentId], cur.window == winTitle,
                       let focEl = state.uidToElement[cur.uid] {
                        if AXUIElementSetAttributeValue(focEl, kAXSelectedTextAttribute as CFString, text as CFTypeRef) == .success {
                            insertedViaAX = true
                        }
                    }
                    if !insertedViaAX && !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): type refused on non-main window \"\(winTitle)\" because CGEvent.postToPid routes keys to the app's AXMainWindow; use op=\"set\" on a target uid or surface=\"web\" MV3 extension bridge")
                    }
                }
            case "menu":
                guard let p = step.effectiveMenuPath, !p.isEmpty else {
                    if !(step.optional ?? false) {
                        stepErrors.append("step \(i+1): menu op requires non-empty 'path' array")
                    }
                    continue
                }
                if !invokeMenuBar(app.pid, path: p) && !(step.optional ?? false) {
                    stepErrors.append("step \(i+1): menu not found")
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
                } else if let idx = step.index, idx >= 1 {
                    if idx > currentElements.count {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                    }
                    if idx <= currentElements.count {
                        targetEl = currentElements[idx - 1]
                        targetNode = currentNodes[idx - 1]
                    }
                } else if let rawName = step.effectiveName?.lowercased() {
                    let roleFilter = step.role?.lowercased()
                    let candidates = rawName.split(separator: "|").map { String($0).trimmingCharacters(in: .whitespaces) }
                    let preferredRoles: Set<String>?
                    if roleFilter != nil {
                        preferredRoles = nil
                    } else if op == "set" {
                        preferredRoles = ["textfield", "textarea", "searchfield", "combobox"]
                    } else if op == "click" {
                        preferredRoles = [
                            "button", "link", "menuitem", "tab", "checkbox", "radiobutton",
                            "popupbutton", "switch", "disclosuretriangle", "row", "cell",
                            "textfield", "textarea", "searchfield", "combobox"
                        ]
                    } else {
                        preferredRoles = nil
                    }
                    func matchInCurrentNodes() -> (AXUIElement, NodeRecord)? {
                        for cand in candidates {
                            if let pref = preferredRoles {
                                if let pos = currentNodes.firstIndex(where: {
                                    pref.contains($0.role) && $0.label.lowercased() == cand
                                }), pos < currentElements.count {
                                    return (currentElements[pos], currentNodes[pos])
                                }
                            }
                            if let exactPos = currentNodes.firstIndex(where: {
                                (roleFilter == nil || $0.role == roleFilter) && $0.label.lowercased() == cand
                            }), exactPos < currentElements.count {
                                return (currentElements[exactPos], currentNodes[exactPos])
                            }
                            if let pref = preferredRoles {
                                if let pos = currentNodes.firstIndex(where: {
                                    pref.contains($0.role) && $0.label.lowercased().contains(cand)
                                }), pos < currentElements.count {
                                    return (currentElements[pos], currentNodes[pos])
                                }
                            }
                            if let subPos = currentNodes.firstIndex(where: {
                                (roleFilter == nil || $0.role == roleFilter) && $0.label.lowercased().contains(cand)
                            }), subPos < currentElements.count {
                                return (currentElements[subPos], currentNodes[subPos])
                            }
                        }
                        return nil
                    }
                    if let found = matchInCurrentNodes() {
                        targetEl = found.0
                        targetNode = found.1
                    } else {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        if let found = matchInCurrentNodes() {
                            targetEl = found.0
                            targetNode = found.1
                        }
                    }
                }

                guard var el = targetEl else {
                    if !(step.optional ?? false) {
                        let requestedTarget = step.effectiveUid ?? step.effectiveName ?? (step.index != nil ? String(step.index!) : "")
                        if requestedTarget.isEmpty {
                            stepErrors.append("step \(i+1) (\(op)): missing target (uid, name, or target)")
                        } else {
                            stepErrors.append("step \(i+1): target not found (\(requestedTarget))")
                        }
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
                    if let rec = targetNode {
                        let liveEnabled = rec.enabled && readBatchedAttrs(el).enabled
                        if !liveEnabled,
                           let disabledOpen = detectDisabledNSOpenPanel(nodes: currentNodes),
                           disabledOpen.uid == rec.uid {
                            if !(step.optional ?? false) {
                                stepErrors.append("step \(i+1): native NSOpenPanel '\(rec.label)' button (\(rec.uid)) is disabled by macOS TCC/AppKit for synthetic AX selection; press key 'escape' to dismiss and use surface=\"web\" step {\"op\": \"upload\", \"uid\": \"...\", \"files\": [...]} via CDP DOM.setFileInputFiles")
                            }
                            continue
                        }
                    }
                    if targetNode?.role == "textfield" || targetNode?.role == "textarea" {
                        _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                    }
                    var err = AXUIElementPerformAction(el, kAXPressAction as CFString)
                    if err == .invalidUIElement || err == .cannotComplete {
                        (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                        if let uid = step.effectiveUid ?? targetNode?.uid, let refreshed = state.uidToElement[uid] {
                            el = refreshed
                            targetNode = state.uidToRecord[uid]
                            if targetNode?.role == "textfield" || targetNode?.role == "textarea" {
                                _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                            }
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
                    guard let textStr = step.effectiveValue else {
                        if !(step.optional ?? false) {
                            stepErrors.append("step \(i+1): set op requires 'value' (or 'text') parameter")
                        }
                        continue
                    }
                    let expectedClean = cleanText(textStr as AnyObject)
                    let role = targetNode?.role ?? ""
                    let subrole = axString(el, kAXSubroleAttribute)
                    let isSecure = (subrole == "AXSecureTextField" || role == "securetextfield")
                    let isTextarea = (role == "textarea")
                    let inWebArea = isElementInWebArea(el)
                    let beforeDesc = isTextarea
                        ? collectDescendantStaticText(el)
                        : (text: "", staticCount: 0, childCount: 0)
                    let beforeRawVal = axString(el, kAXValueAttribute)
                    let beforeEffective = (!beforeDesc.text.isEmpty && (beforeDesc.staticCount > 1 || beforeDesc.text.count > beforeRawVal.count))
                        ? beforeDesc.text
                        : beforeRawVal
                    let isWebRichTextarea = isTextarea && inWebArea && (beforeDesc.childCount > 0)

                    var verified = false
                    var actualAfter = beforeEffective

                    // Pure AX attribute setting (clipboard-free; skipped on multi-block web rich-text to avoid corrupting cached kAXValueAttribute)
                    if !isWebRichTextarea {
                        _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                        var err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, textStr as CFTypeRef)
                        if err == .invalidUIElement || err == .cannotComplete {
                            (currentNodes, currentElements) = walkAXTree(root, winKey: winKey, query: req.query)
                            if let uid = step.effectiveUid ?? targetNode?.uid, let refreshed = state.uidToElement[uid] {
                                el = refreshed
                                targetNode = state.uidToRecord[uid]
                                _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                                err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, textStr as CFTypeRef)
                            }
                        }
                        var check = verifyElementSetValue(
                            el,
                            role: role,
                            inWebArea: inWebArea,
                            isSecure: isSecure,
                            expectedClean: expectedClean,
                            usedPureAXOnlyOnWebRichText: false
                        )
                        if !check.verified {
                            _ = replaceViaAXSelectedText(el, text: textStr)
                            check = verifyElementSetValue(
                                el,
                                role: role,
                                inWebArea: inWebArea,
                                isSecure: isSecure,
                                expectedClean: expectedClean,
                                usedPureAXOnlyOnWebRichText: false
                            )
                        }
                        verified = check.verified
                        actualAfter = check.actual
                    }

                    // Non-destructive UTF-16 CGEvent typing fallback (strictly gated on rootIsMain; clipboard-free)
                    if !verified && rootIsMain {
                        _ = AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
                        _ = AXUIElementPerformAction(el, kAXPressAction as CFString)
                        Thread.sleep(forTimeInterval: 0.020)
                        _ = selectAllViaAX(el)
                        postKeyChordToPid(app.pid, chord: "cmd+a")
                        Thread.sleep(forTimeInterval: 0.015)
                        postKeyChordToPid(app.pid, chord: "backspace")
                        Thread.sleep(forTimeInterval: 0.020)

                        var canType = true
                        if isWebRichTextarea && !beforeEffective.isEmpty {
                            let afterClear = verifyElementSetValue(
                                el,
                                role: role,
                                inWebArea: inWebArea,
                                isSecure: isSecure,
                                expectedClean: "",
                                usedPureAXOnlyOnWebRichText: false
                            )
                            if !afterClear.verified && afterClear.actual.count >= beforeEffective.count {
                                canType = false
                                actualAfter = afterClear.actual
                            }
                        }
                        if canType {
                            if !textStr.isEmpty {
                                postBatchedUnicodeTextToPid(app.pid, text: textStr)
                                Thread.sleep(forTimeInterval: 0.030)
                            }
                            let check = verifyElementSetValue(
                                el,
                                role: role,
                                inWebArea: inWebArea,
                                isSecure: isSecure,
                                expectedClean: expectedClean,
                                usedPureAXOnlyOnWebRichText: false
                            )
                            verified = check.verified
                            actualAfter = check.actual
                        }
                    }

                    if !verified && !(step.optional ?? false) {
                        let uidLabel = targetNode?.uid ?? step.effectiveUid ?? step.effectiveName ?? role
                        let preview = actualAfter.count > 80 ? String(actualAfter.prefix(80)) + "..." : actualAfter
                        if !rootIsMain {
                            stepErrors.append("step \(i+1): set on \(role) \(uidLabel) could not verify value replacement on non-main window \"\(winTitle)\" (actual=\"\(preview)\"); CGEvent typing skipped to avoid leaking keys to AXMainWindow — use surface=\"web\" MV3 extension bridge")
                        } else if inWebArea {
                            stepErrors.append("step \(i+1): set on web \(role) \(uidLabel) could not verify value replacement (actual=\"\(preview)\"); for web rich-text/contenteditable editors in Chrome, use surface=\"web\" with the MV3 extension bridge")
                        } else {
                            stepErrors.append("step \(i+1): set on \(role) \(uidLabel) could not verify value replacement (actual=\"\(preview)\")")
                        }
                    }
                }
            default:
                if !(step.optional ?? false) {
                    stepErrors.append("step \(i+1): unsupported op '\(op)'; supported ops: click, set, move, type, key, menu, wait, settle, ocr")
                }
            }
        }
    }

    if MenuBarController.shared.checkStopped() {
        return userStoppedErrorMessage
    }

    let newCrc: String
    if didMutate {
        let settled = settleAndWalkAXTree(
            root: root,
            winKey: winKey,
            pid: app.pid,
            cachedNodes: currentNodes,
            cachedElements: currentElements,
            prevCrc: prevCrc,
            query: req.query,
            shouldSettle: req.settle ?? true
        )
        currentNodes = settled.0
        currentElements = settled.1
        newCrc = settled.2
        if !hasQuery && !currentNodes.isEmpty {
            state.latestWindowNodes[winKey] = currentNodes
            state.latestWindowElements[winKey] = currentElements
        }
        if let disabledOpen = detectDisabledNSOpenPanel(nodes: currentNodes),
           !stepErrors.contains(where: { $0.contains("NSOpenPanel") }) {
            stepErrors.append("native NSOpenPanel file sheet detected with disabled '\(disabledOpen.label)' button (\(disabledOpen.uid)) — macOS TCC blocks synthetic AX file-panel confirmation; press key 'escape' to dismiss and use surface=\"web\" step {\"op\": \"upload\", \"uid\": \"...\", \"files\": [...]} via CDP DOM.setFileInputFiles")
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

    let rootAttrs = readBatchedAttrs(root)
    let winBounds = CGRect(
        x: rootAttrs.x,
        y: rootAttrs.y,
        width: max(200, rootAttrs.w),
        height: max(140, rootAttrs.h)
    )
    MenuBarController.shared.updateAppWindowPreview(
        pid: app.pid,
        windowID: app.windowID,
        appName: app.name,
        winTitle: winTitle,
        winRect: winBounds,
        nodes: currentNodes,
        cursor: state.agentCursors[agentId],
        forceCapture: didMutate
    )

    let elapsedMs = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000.0
    let hasMoveStep = (req.steps ?? []).contains { $0.effectiveOp == "move" }
    let hasSnapshotStep = (req.steps ?? []).contains { $0.effectiveOp == "snapshot" }
    let hasDiffStep = (req.steps ?? []).contains { $0.effectiveOp == "diff" }
    let explicitDiff: Bool? = req.diff ?? (hasDiffStep ? true : (hasSnapshotStep && !didMutate ? false : nil))
    let useDiff = !hasQuery && (explicitDiff ?? ((didMutate || hasMoveStep) && hadBaseline))

    var out: [String] = []
    let docKey = "\(agentId)|\(app.name)"
    let isFirstDoc = !state.documentedApps.contains(docKey)
    state.documentedApps.insert(docKey)

    var header = "# app=\"\(app.name)\" pid=\(app.pid) win=\"\(winTitle)\""
    if !rootIsMain {
        header += " main=false"
    }
    if agentId != "default" {
        header += " agent=\"\(agentId)\""
    }
    if (agentId != "default" || hasMoveStep), let cur = state.agentCursors[agentId] {
        header += " cursor=\(cur.uid)@(\(cur.x),\(cur.y))"
    }
    header += " els=\(currentNodes.count) crc=\(newCrc) obs=\(state.currentPidSeq(app.pid)) ms=\(String(format: "%.1f", elapsedMs))"
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

enum DecodedBatchRequest {
    case ok(BatchRequest)
    case err(String)
}

func decodeBatchRequestOrError(_ rawData: Data) -> DecodedBatchRequest {
    if let req = try? JSONDecoder().decode(BatchRequest.self, from: rawData) {
        return .ok(req)
    }
    guard let jsonObj = try? JSONSerialization.jsonObject(with: rawData, options: []),
          let dict = jsonObj as? [String: Any] else {
        return .err("Error: invalid JSON request")
    }
    if let stepsVal = dict["steps"], !(stepsVal is NSNull) {
        guard let stepsArr = stepsVal as? [Any] else {
            return .err("Error: 'steps' must be a JSON array of step objects")
        }
        for (idx, item) in stepsArr.enumerated() {
            if !(item is [String: Any]) {
                return .err("Error: step \(idx + 1) must be a JSON object")
            }
        }
    }
    return .err("Error: invalid request schema")
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

    let acceptQueue = DispatchQueue(label: "fmu.daemon.accept", qos: .userInteractive)
    acceptQueue.async {
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
                if let cleanData = trimmed.data(using: .utf8) {
                    switch decodeBatchRequestOrError(cleanData) {
                    case .ok(let req):
                        resp = executeRequest(req)
                    case .err(let errMsg):
                        resp = errMsg
                    }
                } else {
                    resp = "Error: invalid JSON request"
                }
                var outBytes = Array(resp.utf8)
                _ = write(clientFd, &outBytes, outBytes.count)
                close(clientFd)
            }
        }
    }

    if ProcessInfo.processInfo.environment["FMU_MENUBAR"] != "0" {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        MenuBarController.shared.setup(socketPath: socketPath)
        app.run()
    } else {
        dispatchMain()
    }
    exit(0)
}

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "--daemon" {
    let sock = args.count >= 3 ? args[2] : defaultSocketPath
    runDaemon(socketPath: sock)
}

let rawPayload: String
if args.count >= 2 {
    if args[1] == "--reset" || args[1] == "reset" {
        rawPayload = "{\"reset\":true,\"resume\":true}"
    } else if args[1] == "--stop" || args[1] == "stop" {
        rawPayload = "{\"stop\":true}"
    } else if args[1] == "--resume" || args[1] == "resume" {
        rawPayload = "{\"resume\":true}"
    } else if args[1] == "--status" || args[1] == "status" {
        rawPayload = "{\"statusBar\":true}"
    } else if args[1] == "--highlight" || args[1] == "highlight" {
        rawPayload = "{\"highlightWindow\":true}"
    } else {
        rawPayload = args[1]
    }
} else if isatty(STDIN_FILENO) == 0 {
    let stdinData = FileHandle.standardInput.readDataToEndOfFile()
    rawPayload = String(data: stdinData, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    if rawPayload.isEmpty {
        fputs("Usage: fmu-ax '<json-batch>' | fmu-ax --stop | fmu-ax --resume | fmu-ax --status | fmu-ax --reset | fmu-ax --daemon [socket-path]\n", stderr)
        exit(2)
    }
} else {
    fputs("Usage: fmu-ax '<json-batch>' | fmu-ax --stop | fmu-ax --resume | fmu-ax --status | fmu-ax --reset | fmu-ax --daemon [socket-path]\n", stderr)
    exit(2)
}

if let daemonResp = trySendToDaemon(socketPath: defaultSocketPath, payload: rawPayload) {
    print(daemonResp)
    exit(0)
}

guard let reqData = rawPayload.data(using: .utf8) else {
    fputs("Invalid JSON request\n", stderr)
    exit(2)
}

switch decodeBatchRequestOrError(reqData) {
case .ok(let req):
    print(executeRequest(req))
case .err(let errMsg):
    if errMsg == "Error: invalid JSON request" {
        fputs("Invalid JSON request\n", stderr)
        exit(2)
    }
    print(errMsg)
}
