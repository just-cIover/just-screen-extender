import Foundation
import QuartzCore
import UIKit

struct VideoConfig {
    let width: Int
    let height: Int
    let fps: Int
    let avcC: Data
}

protocol ConnectionDelegate: AnyObject {
    func connection(_ c: Connection, status: String?)            // nil = tutto a posto
    func connectionNeedsPin(_ c: Connection, message: String?)
    func connectionReady(_ c: Connection)
    func connection(_ c: Connection, config: VideoConfig)
    func connection(_ c: Connection, frame: Data, key: Bool)
    func connection(_ c: Connection, newFiles names: [String], from sender: String)
}

/// Collegamento cifrato (WebSocket su HTTPS) con il server sul PC: stesso protocollo dell'app web.
final class Connection: NSObject, URLSessionWebSocketDelegate {
    weak var delegate: ConnectionDelegate?
    private(set) var ready = false
    private(set) var rtt: Double?          // ritardo di andata e ritorno in ms
    private(set) var pairing = false
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var generation = 0
    private var retry = 0
    private var lastMessage = Date()
    private var timer: Timer?
    private var holdUntil = Date.distantPast  // abbinamenti bloccati dal PC: niente tentativi a raffica

    override init() {
        super.init()
        let cfg = URLSessionConfiguration.default
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg, delegate: self, delegateQueue: .main)
    }

    // MARK: collegamento

    func connect() {
        close()
        let host = Settings.host
        guard !host.isEmpty else { return }
        var comps = URLComponents()
        comps.scheme = "wss"
        let parts = host.split(separator: ":", maxSplits: 1)
        comps.host = String(parts[0])
        comps.port = parts.count > 1 ? Int(parts[1]) : 8770
        comps.path = "/ws"
        comps.queryItems = [URLQueryItem(name: "dev", value: Settings.deviceId),
                            URLQueryItem(name: "token", value: Settings.token)]
        guard let url = comps.url else {
            delegate?.connection(self, status: "Indirizzo del PC non valido: \(host)")
            return
        }
        generation += 1
        let gen = generation
        let t = session.webSocketTask(with: url)
        t.maximumMessageSize = 16 << 20
        task = t
        t.resume()
        receive(gen)
        lastMessage = Date()
        delegate?.connection(self, status: retry > 0 ? "Riconnessione…" : "Connessione al PC…")
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
    }

    func close() {
        generation += 1
        timer?.invalidate()
        timer = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        ready = false
    }

    private func tick() {
        if ready { send(["t": "ping", "ts": CACurrentMediaTime() * 1000]) }
        // nessun dato da 4 s (Wi-Fi addormentato, PC in pausa…): riparto
        if !pairing && Date().timeIntervalSince(lastMessage) > 4 { lost() }
    }

    private func lost() {
        let wasPairing = pairing
        close()
        pairing = false
        if wasPairing && Date() < holdUntil {
            DispatchQueue.main.asyncAfter(deadline: .now() + min(holdUntil.timeIntervalSinceNow, 60)) { [weak self] in
                self?.connect()
            }
            return
        }
        retry += 1
        delegate?.connection(self, status: "Connessione persa, riprovo…")
        DispatchQueue.main.asyncAfter(deadline: .now() + min(0.3 * Double(retry), 2)) { [weak self] in
            guard let self, self.task == nil else { return }
            self.connect()
        }
    }

    private func receive(_ gen: Int) {
        task?.receive { [weak self] result in
            guard let self, gen == self.generation else { return }
            switch result {
            case .failure:
                self.lost()
            case .success(let message):
                self.lastMessage = Date()
                switch message {
                case .data(let d):
                    if let first = d.first, d.count > 1 {
                        self.delegate?.connection(self, frame: d.subdata(in: (d.startIndex + 1)..<d.endIndex), key: first == 1)
                    }
                case .string(let s):
                    self.handle(s)
                @unknown default:
                    break
                }
                self.receive(gen)
            }
        }
    }

    // MARK: messaggi dal PC

    private func handle(_ text: String) {
        guard let data = text.data(using: .utf8),
              let m = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let t = m["t"] as? String else { return }
        switch t {
        case "pair":
            pairing = true
            send(["t": "hello", "nome": UIDevice.current.model])  // "iPad": lo stesso nome dell'app web
            delegate?.connectionNeedsPin(self, message: nil)
        case "paired":
            if let token = m["token"] as? String { Settings.token = token }
        case "pinerr":
            if let secs = m["bloccato"] as? Int {
                holdUntil = Date().addingTimeInterval(Double(secs))
                delegate?.connectionNeedsPin(self, message: "Troppi tentativi sbagliati: riprova tra \(Int(ceil(Double(secs) / 60))) minuti.")
            } else if m["occupato"] as? Bool == true {
                holdUntil = Date().addingTimeInterval(30)
                delegate?.connectionNeedsPin(self, message: "Troppe richieste di abbinamento in corso: riprova tra poco.")
            } else if m["nuovo"] as? Bool == true {
                delegate?.connectionNeedsPin(self, message: "Il PIN è cambiato: inserisci quello nuovo che vedi sul PC.")
            } else {
                delegate?.connectionNeedsPin(self, message: "PIN errato, riprova.")
            }
        case "hello":
            pairing = false
            ready = true
            retry = 0
            delegate?.connection(self, status: nil)
            delegate?.connectionReady(self)
        case "config":
            guard let w = m["w"] as? Int, let h = m["h"] as? Int,
                  let b64 = m["desc"] as? String, let avcC = Data(base64Encoded: b64) else { return }
            delegate?.connection(self, config: VideoConfig(width: w, height: h, fps: m["fps"] as? Int ?? 30, avcC: avcC))
        case "quality":
            // la qualità la ricorda il PC (e la può imporre): la tengo allineata nel menu
            if let level = m["level"] as? Int { Settings.quality = level }
        case "pong":
            if let ts = m["ts"] as? Double { rtt = CACurrentMediaTime() * 1000 - ts }
        case "file":
            let names = m["nomi"] as? [String] ?? []
            if !names.isEmpty { delegate?.connection(self, newFiles: names, from: m["da"] as? String ?? "PC") }
        case "full":
            delegate?.connection(self, status: "Il PC ha già il numero massimo di dispositivi abbinati: rimuovine uno dal PC.")
        default:
            break
        }
    }

    // MARK: messaggi verso il PC

    func send(_ obj: [String: Any]) {
        guard let task, let data = try? JSONSerialization.data(withJSONObject: obj),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { _ in }
    }

    func sendPin(_ pin: String) {
        let screen = UIScreen.main.nativeBounds.size  // pixel reali
        send(["t": "pin", "pin": pin, "nome": UIDevice.current.model,
              "w": Int(max(screen.width, screen.height)), "h": Int(min(screen.width, screen.height)),
              "dpr": Double(UIScreen.main.nativeScale)])
    }

    /// Dimenticare la chiave: al prossimo collegamento il PC chiederà un PIN nuovo.
    func forgetPairing() {
        Settings.token = ""
        connect()
    }
}
