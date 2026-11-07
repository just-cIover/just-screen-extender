import Foundation
import Security

/// App settings. The key received from the PC after the PIN is in the Keychain (encrypted),
/// the rest in the normal settings.
enum Settings {
    private static let d = UserDefaults.standard

    /// PC address, e.g. "192.168.1.20:8770".
    static var host: String {
        get { d.string(forKey: "host") ?? "" }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "host") }
    }

    /// All the PC addresses seen so far (home network, hotspot…): if the current one doesn't answer
    /// the app tries them one by one.
    static var knownHosts: [String] {
        get { d.stringArray(forKey: "knownHosts") ?? [] }
        set { d.set(Array(newValue.prefix(12)), forKey: "knownHosts") }
    }

    /// Random code of this device, created the first time.
    static var deviceId: String {
        if let id = d.string(forKey: "devId") { return id }
        let id = UUID().uuidString.lowercased()
        d.set(id, forKey: "devId")
        return id
    }

    static var token: String {
        get { Keychain.read("token") ?? "" }
        set { Keychain.write("token", newValue) }
    }

    /// 0 Best (60 fps) … 3 Light. Smooth by default: on older iPads 60 fps at full resolution is too much.
    static var quality: Int {
        get { d.object(forKey: "quality") as? Int ?? 1 }
        set { d.set(newValue, forKey: "quality") }
    }

    /// "touch" (Windows touch) or "mouse".
    static var touchMode: String {
        get { d.string(forKey: "touchMode") ?? "touch" }
        set { d.set(newValue, forKey: "touchMode") }
    }

    /// "auto" (writes in drawing programs, acts as a finger elsewhere), "penna", "gomma" or "mouse".
    static var penMode: String {
        get { d.string(forKey: "penMode2") ?? "auto" }
        set { d.set(newValue, forKey: "penMode2") }
    }

    /// The PC screen rotates with the device (portrait/landscape). Off = always landscape.
    static var rotate: Bool {
        get { d.object(forKey: "rotate") as? Bool ?? true }
        set { d.set(newValue, forKey: "rotate") }
    }

    /// Where the menu button is (moved by dragging it): 0…7 = corners and middles of the edges, see ScreenViewController.
    static var menuSpot: Int {
        get { d.object(forKey: "menuSpot") as? Int ?? 2 }  // 2 = top right
        set { d.set(newValue, forKey: "menuSpot") }
    }

    /// Clipboard shared with the PC.
    static var clipboardSync: Bool {
        get { d.object(forKey: "clipboardSync") as? Bool ?? true }
        set { d.set(newValue, forKey: "clipboardSync") }
    }

    /// Last iPad clipboard state already seen (to send only new copies to the PC).
    static var pasteboardSeen: Int {
        get { d.object(forKey: "pasteboardSeen") as? Int ?? -1 }
        set { d.set(newValue, forKey: "pasteboardSeen") }
    }

    /// App language: "it" or "en". Before the first connection the device's own, then the PC's.
    static var language: String {
        get { d.string(forKey: "lingua") ?? (Locale.preferredLanguages.first?.hasPrefix("it") == true ? "it" : "en") }
        set { d.set(newValue, forKey: "lingua") }
    }

    /// Instant ink: the Pencil stroke appears right away, before the real one arrives from the PC.
    static var instantInk: Bool {
        get { d.object(forKey: "instantInk") as? Bool ?? true }
        set { d.set(newValue, forKey: "instantInk") }
    }
}

enum Keychain {
    private static let service = "it.schermoesteso"

    static func read(_ key: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: key,
                                kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ key: String, _ value: String) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                   kSecAttrService as String: service,
                                   kSecAttrAccount as String: key]
        SecItemDelete(base as CFDictionary)
        guard !value.isEmpty else { return }
        var add = base
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(add as CFDictionary, nil)
    }
}
