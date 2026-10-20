import Foundation
import Security

/// Impostazioni dell'app. La chiave ricevuta dal PC dopo il PIN sta nel Portachiavi (cifrata),
/// il resto nelle impostazioni normali.
enum Settings {
    private static let d = UserDefaults.standard

    /// Indirizzo del PC, es. "192.168.2.99:8770".
    static var host: String {
        get { d.string(forKey: "host") ?? "" }
        set { d.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "host") }
    }

    /// Tutti gli indirizzi del PC visti finora (rete di casa, hotspot…): se quello attuale non risponde
    /// l'app li prova uno per uno.
    static var knownHosts: [String] {
        get { d.stringArray(forKey: "knownHosts") ?? [] }
        set { d.set(Array(newValue.prefix(12)), forKey: "knownHosts") }
    }

    /// Codice casuale di questo dispositivo, creato la prima volta.
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

    /// 0 Massima (60 fps) … 3 Leggera. Di base Fluida: su iPad meno recenti 60 fps a piena risoluzione sono troppi.
    static var quality: Int {
        get { d.object(forKey: "quality") as? Int ?? 1 }
        set { d.set(newValue, forKey: "quality") }
    }

    /// "touch" (tocco di Windows) oppure "mouse".
    static var touchMode: String {
        get { d.string(forKey: "touchMode") ?? "touch" }
        set { d.set(newValue, forKey: "touchMode") }
    }

    /// "auto" (scrive nei programmi da disegno, altrove fa da dito), "penna", "gomma" oppure "mouse".
    static var penMode: String {
        get { d.string(forKey: "penMode2") ?? "auto" }
        set { d.set(newValue, forKey: "penMode2") }
    }

    /// Lo schermo del PC gira con il dispositivo (verticale/orizzontale). Spento = sempre orizzontale.
    static var rotate: Bool {
        get { d.object(forKey: "rotate") as? Bool ?? true }
        set { d.set(newValue, forKey: "rotate") }
    }

    /// Appunti condivisi con il PC.
    static var clipboardSync: Bool {
        get { d.object(forKey: "clipboardSync") as? Bool ?? true }
        set { d.set(newValue, forKey: "clipboardSync") }
    }

    /// Ultimo stato degli appunti dell'iPad già visto (per mandare al PC solo le copie nuove).
    static var pasteboardSeen: Int {
        get { d.object(forKey: "pasteboardSeen") as? Int ?? -1 }
        set { d.set(newValue, forKey: "pasteboardSeen") }
    }

    /// Inchiostro immediato: il tratto della Pencil compare subito, prima che arrivi quello vero dal PC.
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
