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
        Thread.sleep(forTimeInterval: 0.010)
    }
    var (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
    var curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
    if shouldSettle {
        for _ in 0..<2 where curCrc == prevCrc {
            Thread.sleep(forTimeInterval: 0.015)
            (nodes, els) = walkAXTree(root, winKey: winKey, query: query)
            curCrc = crc32(nodes.map { $0.compactLine }.joined(separator: "\n"))
        }
    }
    _ = state.consumeDirtyState(pid)
    state.windowSyncedSeq[winKey] = state.currentPidSeq(pid)
    return (nodes, els, curCrc)
}

func executeRequest(_ req: BatchRequest) -> String {
    let t0 = DispatchTime.now().uptimeNanoseconds
    if let imgPath = req.imagePath, !imgPath.isEmpty {
        return performImageFileOCR(
            imagePath: imgPath,
            clipX: req.clipX ?? 0.0,
            clipY: req.clipY ?? 0.0,
            clipW: req.clipW ?? 0.0,
            clipH: req.clipH ?? 0.0
        )
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
            let op = step.effectiveOp
            if op.isEmpty {
                stepErrors.append("step \(i+1): missing 'op' (or 'action') field in step; supported ops: click, set, move, type, key, menu, wait, settle, ocr")
                continue
            }
            if op == "upload" {
                stepErrors.append("step \(i+1): op 'upload' is only supported on surface=\"web\" via the MV3 extension CDP bridge (DOM.setFileInputFiles)")
                continue
            }
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
                guard let p = step.path, !p.isEmpty else {
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

    let elapsedMs = Double(DispatchTime.now().uptimeNanoseconds - t0) / 1_000_000.0
    let hasMoveStep = (req.steps ?? []).contains { $0.effectiveOp == "move" }
    let useDiff = !hasQuery && (req.diff ?? ((didMutate || hasMoveStep) && hadBaseline))

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
