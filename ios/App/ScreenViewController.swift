import AVFoundation
import UIKit
import UniformTypeIdentifiers

/// Il video del PC: decodificato e disegnato dall'hardware dell'iPad.
final class VideoView: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    var display: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
}

/// Inchiostro immediato: il tratto della Pencil compare subito e sfuma quando arriva quello vero
/// dal PC. In più disegna i punti "previsti" da iPadOS, che anticipano il movimento della punta.
final class InkView: UIView {
    private struct Segment { let a: CGPoint; let b: CGPoint; let width: CGFloat; let time: CFTimeInterval }
    private var segments: [Segment] = []
    private var predicted: [CGPoint] = []
    private var link: CADisplayLink?
    var lifetime: () -> Double = { 0.25 }  // secondi di visibilità (circa il ritardo del collegamento)

    override init(frame: CGRect) {
        super.init(frame: frame)
        isOpaque = false
        backgroundColor = .clear
        isUserInteractionEnabled = false
        contentMode = .redraw
    }

    required init?(coder: NSCoder) { fatalError("non usato") }

    func add(from a: CGPoint, to b: CGPoint, width: CGFloat) {
        segments.append(Segment(a: a, b: b, width: width, time: CACurrentMediaTime()))
        run()
    }

    func setPredicted(_ points: [CGPoint]) { predicted = points; run() }

    func endStroke() { predicted = []; setNeedsDisplay() }

    /// Toglie subito tutto l'inchiostro (la Pencil sta scorrendo, non scrivendo).
    func clear() { segments = []; predicted = []; setNeedsDisplay() }

    private func run() {
        if link == nil {
            link = CADisplayLink(target: self, selector: #selector(step))
            link?.add(to: .main, forMode: .common)
        }
        setNeedsDisplay()
    }

    @objc private func step() {
        let now = CACurrentMediaTime(), life = lifetime()
        segments.removeAll { now - $0.time > life }
        if segments.isEmpty && predicted.isEmpty {
            link?.invalidate()
            link = nil
        }
        setNeedsDisplay()
    }

    override func draw(_ rect: CGRect) {
        guard let ctx = UIGraphicsGetCurrentContext() else { return }
        ctx.setLineCap(.round)
        let now = CACurrentMediaTime(), life = lifetime()
        let ink = UIColor(red: 0.29, green: 0.31, blue: 0.35, alpha: 1)
        for s in segments {
            let alpha = max(0, min(1, 2 * (1 - (now - s.time) / life))) * 0.9
            ctx.setStrokeColor(ink.withAlphaComponent(alpha).cgColor)
            ctx.setLineWidth(s.width)
            ctx.move(to: s.a)
            ctx.addLine(to: s.b)
            ctx.strokePath()
        }
        if predicted.count > 1 {
            ctx.setStrokeColor(ink.withAlphaComponent(0.45).cgColor)
            ctx.setLineWidth(1.5)
            ctx.addLines(between: predicted)
            ctx.strokePath()
        }
    }
}

final class ScreenViewController: UIViewController, ConnectionDelegate, UIEditMenuInteractionDelegate,
                                  UIDropInteractionDelegate {
    let connection = Connection()
    private let video = VideoView()
    private let ink = InkView()
    private let statusLabel = UILabel()
    private let spinner = UIActivityIndicatorView(style: .large)
    private let toast = UILabel()
    private let menuButton = UIButton(type: .system)

    private var format: CMVideoFormatDescription?
    private var videoSize = CGSize(width: 4, height: 3)
    private var waitingKey = true
    private var framesShown = 0
    private var bytesReceived = 0
    private var statsTimer: Timer?
    private var active = false
    private weak var pinAlert: UIAlertController?
    private weak var filesPanel: FilesViewController?
    private var pendingShared: [URL] = []  // file arrivati da altre app prima che il PC fosse configurato
    private static let qualityNames = ["Massima (60 fps)", "Fluida (30 fps)", "Media (75%)", "Leggera (50%)"]

    // tocco
    private var fingers: [ObjectIdentifier: (id: Int, x: Double, y: Double)] = [:]
    private var nextTouchId = 1
    // Pencil
    private var penTouch: UITouch?
    private var penUpAt: CFTimeInterval = 0
    private var lastPenPoint: CGPoint?
    private var penFinger = false  // in automatica: in questo tratto la Pencil fa da dito (niente inchiostro)
    // doppio tocco = seleziona la parola (come sull'iPad)
    private var tapStarts: [ObjectIdentifier: (time: CFTimeInterval, point: CGPoint)] = [:]
    private var lastTap: (time: CFTimeInterval, point: CGPoint)?
    private var editMenu: UIEditMenuInteraction?
    // un dito solo appena appoggiato: non va a Windows finché non si capisce se è un tocco,
    // un trascinamento o una pressione lunga (così tenere premuto non tocca niente sul PC)
    private var pending: (key: ObjectIdentifier, point: CGPoint, time: CFTimeInterval)?
    private var longPress: Timer?
    private var longPressed: ObjectIdentifier?  // dito tenuto premuto: il fumetto è aperto, il resto lo ignoro
    private var menuPoint = CGPoint.zero        // dove mostrare il fumetto (e dove fare il clic destro)
    // zoom con due dita: solo l'immagine sul dispositivo, Windows non cambia
    private var zoom: CGFloat = 1
    private var pan = CGPoint.zero
    private var two: (a: ObjectIdentifier, b: ObjectIdentifier, a0: CGPoint, b0: CGPoint)?  // pizzico o scorrimento?
    private var pinching: (a: ObjectIdentifier, b: ObjectIdentifier, zoom0: CGFloat, d0: CGFloat, q0: CGPoint)?
    private var ignored = Set<ObjectIdentifier>()  // dita di un gesto già finito (o terze dita)
    // modalità mouse con le dita
    private var mouseFingers: [UITouch] = []
    private var dragging = false
    private var twoStart: CFTimeInterval = 0
    private var twoMoved = false
    private var lastMid: CGPoint?

    override func loadView() { view = UIView() }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        // il video sta in un contenitore: così lo posso ingrandire senza ingrandire inchiostro, menu e avvisi
        video.backgroundColor = .black
        video.isUserInteractionEnabled = false
        view.addSubview(video)
        view.addInteraction(UIDropInteraction(delegate: self))
        view.isMultipleTouchEnabled = true
        video.display.videoGravity = .resizeAspect
        connection.delegate = self
        connection.onClipboard = { [weak self] m in self?.clipboardFromPC(m) }
        connection.onNotice = { [weak self] text in self?.showToast(text) }
        connection.onWordSelected = { [weak self] in self?.showEditMenu() }
        connection.onPenUse = { [weak self] use in
            // il PC ha deciso: sotto la punta non c'è un programma da disegno, quindi scorre e tocca
            guard let self else { return }
            self.penFinger = use == "dito"
            if self.penFinger { self.ink.clear() }
        }

        let menu = UIEditMenuInteraction(delegate: self)
        view.addInteraction(menu)
        editMenu = menu

        ink.frame = view.bounds
        ink.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        ink.lifetime = { [weak self] in min(0.6, max(0.15, ((self?.connection.rtt ?? 60) + 120) / 1000)) }
        view.addSubview(ink)

        statusLabel.textColor = UIColor(white: 0.7, alpha: 1)
        statusLabel.font = .systemFont(ofSize: 17)
        statusLabel.numberOfLines = 0
        statusLabel.textAlignment = .center
        spinner.color = UIColor(red: 0.95, green: 0.72, blue: 0.29, alpha: 1)
        toast.backgroundColor = UIColor(red: 0.08, green: 0.09, blue: 0.11, alpha: 0.95)
        toast.textColor = .white
        toast.font = .systemFont(ofSize: 15, weight: .semibold)
        toast.textAlignment = .center
        toast.layer.cornerRadius = 12
        toast.layer.masksToBounds = true
        toast.alpha = 0

        var cfg = UIButton.Configuration.filled()
        cfg.image = UIImage(systemName: "line.3.horizontal")
        cfg.baseBackgroundColor = UIColor(white: 0.1, alpha: 0.35)
        cfg.baseForegroundColor = UIColor(white: 1, alpha: 0.6)
        cfg.cornerStyle = .capsule
        menuButton.configuration = cfg
        menuButton.showsMenuAsPrimaryAction = true
        menuButton.menu = UIMenu(children: [UIDeferredMenuElement.uncached { [weak self] done in
            done(self?.menuItems() ?? [])
        }])
        menuButton.accessibilityLabel = "Menu"

        for v in [statusLabel, spinner, toast, menuButton] as [UIView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(v)
        }
        NSLayoutConstraint.activate([
            spinner.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            spinner.centerYAnchor.constraint(equalTo: view.centerYAnchor, constant: -20),
            statusLabel.topAnchor.constraint(equalTo: spinner.bottomAnchor, constant: 14),
            statusLabel.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            statusLabel.widthAnchor.constraint(lessThanOrEqualTo: view.widthAnchor, constant: -48),
            toast.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -16),
            toast.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            toast.heightAnchor.constraint(equalToConstant: 44),
            toast.widthAnchor.constraint(lessThanOrEqualTo: view.widthAnchor, constant: -32),
            menuButton.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 6),
            menuButton.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -6),
            menuButton.widthAnchor.constraint(equalToConstant: 44),
            menuButton.heightAnchor.constraint(equalToConstant: 44),
        ])
        showStatus("Avvio…")
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        video.bounds = view.bounds
        video.center = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        applyZoom()
    }

    // schermo intero vero: niente barra di iPadOS, indicatore Home nascosto, bordi meno sensibili
    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var preferredScreenEdgesDeferringSystemGestures: UIRectEdge { .all }

    // MARK: ciclo di vita (chiamato dalla scena)

    func resume() {
        guard !active else { return }
        active = true
        if connection.ready { clipboardToPC(force: false) }
        if Settings.host.isEmpty {
            askHost()
        } else {
            connection.connect()
        }
        statsTimer?.invalidate()
        statsTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.sendStats() }
    }

    func pause() {
        active = false
        statsTimer?.invalidate()
        connection.close()
        UIApplication.shared.isIdleTimerDisabled = false
        video.display.flush()
        releaseInput()
    }

    // MARK: ConnectionDelegate

    func connection(_ c: Connection, status: String?) {
        if let status { showStatus(status) } else { hideStatus() }
    }

    func connectionNeedsPin(_ c: Connection, message: String?) {
        hideStatus()
        let text = (message.map { $0 + "\n\n" } ?? "") + "Questo \(UIDevice.current.model) è nuovo per il PC: inserisci il PIN di 6 cifre che compare ora nella finestra di Schermo Esteso."
        if let alert = pinAlert {
            alert.message = text
            alert.textFields?.first?.text = ""
            return
        }
        let alert = UIAlertController(title: "Abbinamento", message: text, preferredStyle: .alert)
        alert.addTextField { f in
            f.keyboardType = .numberPad
            f.textContentType = .oneTimeCode
            f.placeholder = "PIN"
            f.textAlignment = .center
        }
        alert.addAction(UIAlertAction(title: "Cambia PC", style: .default) { [weak self] _ in self?.askHost() })
        alert.addAction(UIAlertAction(title: "Collega", style: .default) { [weak self, weak alert] _ in
            let pin = (alert?.textFields?.first?.text ?? "").filter(\.isNumber)
            self?.connection.sendPin(pin)
            self?.showStatus("Verifica del PIN…")
        })
        pinAlert = alert
        present(alert, animated: true)
    }

    func connectionReady(_ c: Connection) {
        pinAlert?.dismiss(animated: true)
        clipboardToPC(force: false)
        if !pendingShared.isEmpty {
            let waiting = pendingShared
            pendingShared = []
            receiveShared(waiting)
        }
        UIApplication.shared.isIdleTimerDisabled = true  // il dispositivo non va in standby mentre fa da schermo
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"  // es. 1.0.12 (l'ultima cifra = compilazione)
        let device = UIDevice.current
        c.send(["t": "diag", "motore": "nativo", "avvio": true,
                "ua": "Schermo Esteso iOS \(version), \(device.model) \(device.systemName) \(device.systemVersion)"])
        sendArea()
    }

    /// Pixel utili: tutto lo schermo, tranne ai lati la tacca o la Dynamic Island dell'iPhone in orizzontale.
    /// Lo schermo virtuale prende esattamente queste misure (su iPad = tutto lo schermo).
    private func sendArea() {
        guard connection.ready else { return }
        let b = view.bounds, i = view.safeAreaInsets, scale = UIScreen.main.nativeScale
        let w = (b.width - i.left - i.right) * scale, h = b.height * scale
        let even = { (v: CGFloat) in Int(v.rounded()) & ~1 }  // la codifica video vuole misure pari
        connection.send(["t": "area", "w": even(max(w, h)), "h": even(min(w, h))])
    }

    override func viewSafeAreaInsetsDidChange() {
        super.viewSafeAreaInsetsDidChange()
        sendArea()
    }

    func connection(_ c: Connection, config: VideoConfig) {
        format = H264.formatDescription(avcC: config.avcC)
        videoSize = CGSize(width: config.width, height: config.height)
        waitingKey = true
        video.display.flush()
        if format == nil { c.send(["t": "diag", "errore": "configurazione video non valida"]) }
    }

    func connection(_ c: Connection, frame: Data, key: Bool) {
        bytesReceived += frame.count
        guard let format else { return }
        if waitingKey && !key { return }
        let display = video.display
        if display.status == .failed {  // il decodificatore si è fermato: riparto dal prossimo fotogramma completo
            display.flush()
            waitingKey = true
            c.send(["t": "key"])
            return
        }
        guard let sample = H264.sampleBuffer(frame, format: format) else { return }
        display.enqueue(sample)
        waitingKey = false
        framesShown += 1
        if !statusLabel.isHidden { hideStatus() }
    }

    func connection(_ c: Connection, newFiles names: [String], from sender: String) {
        showToast(names.count == 1 ? "Nuovo file da \(sender): \(names[0])" : "\(names.count) nuovi file da \(sender)")
        filesPanel?.reload()
    }

    // MARK: statistiche per la finestra del PC

    /// L'app non è in primo piano (Centro di controllo, app switcher, notifica): fotogrammi e ritardo di
    /// questi secondi non dicono niente sulla rete, quindi il PC non li deve usare per la qualità automatica.
    private var quietUntil: CFTimeInterval = 0
    private var inactive = false

    func resignedActive() {
        inactive = true
    }

    func becameActive() {
        inactive = false
        quietUntil = CACurrentMediaTime() + 4
        connection.forgetDelay()
    }

    private func sendStats() {
        guard connection.ready else { return }
        if inactive || CACurrentMediaTime() < quietUntil {
            connection.send(["t": "stats", "pausa": true])
            framesShown = 0
            bytesReceived = 0
            return
        }
        let q = Settings.quality
        connection.send(["t": "stats", "fps": framesShown / 2, "dec": framesShown / 2,
                         "rtt": connection.rtt.map { Int($0) as Any } ?? NSNull(),  // JSON: null se non misurato
                         "mbit": Double(bytesReceived * 8) / 2e6, "render": "nativo/hardware",
                         "q": ["Massima", "Fluida", "Media", "Leggera"][max(0, min(q, 3))]])
        framesShown = 0
        bytesReceived = 0
    }

    // MARK: coordinate

    private var contentRect: CGRect { AVMakeRect(aspectRatio: videoSize, insideRect: video.bounds) }

    private func norm(_ p: CGPoint) -> (x: Double, y: Double) {
        let q = video.convert(p, from: view)  // tiene conto dello zoom
        let r = contentRect
        guard r.width > 0, r.height > 0 else { return (0, 0) }
        return (Double((q.x - r.minX) / r.width), Double((q.y - r.minY) / r.height))
    }

    // MARK: tocchi

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        editMenu?.dismissMenu()
        let fingersOnly = touches.allSatisfy { $0.type != .pencil }
        if fingersOnly && Settings.touchMode != "mouse" && !penBusy && touches.count == 2 && fingers.isEmpty
            && pending == nil && two == nil && pinching == nil {
            // due dita appoggiate insieme: aspetto di capire se è un pizzico (zoom) o uno scorrimento
            let t = Array(touches)
            two = (ObjectIdentifier(t[0]), ObjectIdentifier(t[1]), t[0].location(in: view), t[1].location(in: view))
            return
        }
        for t in touches {
            let key = ObjectIdentifier(t)
            if t.type == .pencil {
                penBegan(t)
            } else if penBusy {
                continue  // palmo appoggiato mentre si scrive con la Pencil
            } else if Settings.touchMode == "mouse" {
                mouseBegan(t)
            } else if two != nil || pinching != nil {
                ignored.insert(key)  // terzo dito durante un pizzico
            } else if let p = pending, fingers.isEmpty {
                // secondo dito mentre il primo aspetta: pizzico (zoom qui) o scorrimento a due dita (Windows)?
                longPress?.invalidate()
                pending = nil
                two = (p.key, key, p.point, t.location(in: view))
            } else if fingers.isEmpty && pending == nil && touches.count == 1 {
                pending = (key, t.location(in: view), CACurrentMediaTime())
                startLongPress(key)
            } else {
                flushPending()  // secondo dito (zoom, scorrimento a due dita): il primo parte subito
                fingerDown(key, at: t.location(in: view))
            }
        }
    }

    private func fingerDown(_ key: ObjectIdentifier, at p: CGPoint) {
        let id = nextTouchId
        nextTouchId += 1
        let n = norm(p)
        fingers[key] = (id, n.x, n.y)
        connection.send(["t": "p", "e": "d", "id": id, "x": n.x, "y": n.y])
    }

    /// Il dito in attesa diventa un tocco su Windows, dal punto in cui è stato appoggiato.
    private func flushPending() {
        guard let p = pending else { return }
        pending = nil
        longPress?.invalidate()
        lastTap = nil
        fingerDown(p.key, at: p.point)
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for t in touches {
            let key = ObjectIdentifier(t)
            if t.type == .pencil {
                penMoved(t, event)
                continue
            } else if Settings.touchMode == "mouse" {
                mouseMoved(t)
                continue
            } else if key == longPressed || ignored.contains(key) {
                continue
            } else if let g = two, key == g.a || key == g.b {
                twoMoved(g, event)
                continue
            } else if let g = pinching, key == g.a || key == g.b {
                pinchMoved(g, event)
                continue
            }
            if let p = pending, p.key == key {
                let now = t.location(in: view)
                guard hypot(now.x - p.point.x, now.y - p.point.y) > 12 else { continue }
                flushPending()  // si muove: scorrimento o trascinamento
            }
            if var f = fingers[key] {
                let n = norm(t.location(in: view))
                f.x = n.x
                f.y = n.y
                fingers[key] = f
                connection.send(["t": "p", "e": "m", "id": f.id, "x": n.x, "y": n.y])
            }
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { finish(touches, cancelled: false) }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { finish(touches, cancelled: true) }

    private func finish(_ touches: Set<UITouch>, cancelled: Bool) {
        for t in touches {
            let key = ObjectIdentifier(t)
            if t.type == .pencil {
                penEnded(t, cancelled: cancelled)
            } else if Settings.touchMode == "mouse" {
                mouseEnded(t)
            } else if ignored.remove(key) != nil {
                continue
            } else if let g = two, key == g.a || key == g.b {
                // due dita sollevate senza muoverle: clic destro, come il tocco a due dita del trackpad
                two = nil
                ignored.insert(key == g.a ? g.b : g.a)
                if !cancelled {
                    let n = norm(CGPoint(x: (g.a0.x + g.b0.x) / 2, y: (g.a0.y + g.b0.y) / 2))
                    connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])
                }
            } else if let g = pinching, key == g.a || key == g.b {
                pinching = nil
                ignored.insert(key == g.a ? g.b : g.a)
                if zoom < 1.08 { setZoom(1, CGPoint.zero, animated: true) }  // quasi al 100%: lo rimetto preciso
            } else if let p = pending, p.key == key {
                pending = nil
                longPress?.invalidate()
                guard !cancelled else { continue }
                let now = CACurrentMediaTime(), end = t.location(in: view)
                let quick = now - p.time < 0.35
                if quick, let last = lastTap, now - last.time < 0.5,
                   hypot(end.x - last.point.x, end.y - last.point.y) < 30 {
                    // secondo tocco di un doppio tocco: non lo mando, decide il PC come selezionare
                    lastTap = nil
                    selectWord(at: end, delivered: false)
                } else {
                    let id = nextTouchId
                    nextTouchId += 1
                    let n = norm(p.point)
                    connection.send(["t": "p", "e": "d", "id": id, "x": n.x, "y": n.y])
                    connection.send(["t": "p", "e": "u", "id": id, "x": n.x, "y": n.y])
                    lastTap = quick ? (now, end) : nil
                }
            } else if key == longPressed {
                longPressed = nil
            } else if let f = fingers.removeValue(forKey: key) {
                let n = norm(t.location(in: view))
                connection.send(["t": "p", "e": cancelled ? "c" : "u", "id": f.id, "x": n.x, "y": n.y])
                lastTap = nil
            }
        }
    }

    private func releaseInput() {
        for f in fingers.values { connection.send(["t": "p", "e": "c", "id": f.id, "x": f.x, "y": f.y]) }
        fingers.removeAll()
        pending = nil
        longPressed = nil
        longPress?.invalidate()
        two = nil
        pinching = nil
        ignored.removeAll()
        mouseFingers.removeAll()
        dragging = false
        penTouch = nil
        lastPenPoint = nil
        ink.endStroke()
    }

    // MARK: zoom con due dita (solo sul dispositivo)

    private func positions(_ a: ObjectIdentifier, _ b: ObjectIdentifier, _ event: UIEvent?) -> (CGPoint, CGPoint)? {
        let all = event?.allTouches ?? []
        guard let ta = all.first(where: { ObjectIdentifier($0) == a }),
              let tb = all.first(where: { ObjectIdentifier($0) == b }) else { return nil }
        return (ta.location(in: view), tb.location(in: view))
    }

    private func twoMoved(_ g: (a: ObjectIdentifier, b: ObjectIdentifier, a0: CGPoint, b0: CGPoint), _ event: UIEvent?) {
        guard let pos = positions(g.a, g.b, event) else { return }
        let (pa, pb) = pos
        let d0 = hypot(g.a0.x - g.b0.x, g.a0.y - g.b0.y), d = hypot(pa.x - pb.x, pa.y - pb.y)
        let m0 = CGPoint(x: (g.a0.x + g.b0.x) / 2, y: (g.a0.y + g.b0.y) / 2)
        let m = CGPoint(x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2)
        if abs(d - d0) > 20 || (zoom > 1.01 && hypot(m.x - m0.x, m.y - m0.y) > 8) {
            // le dita si allargano o si stringono (o l'immagine è già ingrandita): zoom qui
            two = nil
            let c = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
            let q0 = CGPoint(x: c.x + (m.x - c.x - pan.x) / zoom, y: c.y + (m.y - c.y - pan.y) / zoom)
            pinching = (g.a, g.b, zoom, max(d, 1), q0)
        } else if hypot(m.x - m0.x, m.y - m0.y) > 12 {
            // si muovono insieme: scorrimento a due dita di Windows, dai punti in cui sono state appoggiate
            two = nil
            fingerDown(g.a, at: g.a0)
            fingerDown(g.b, at: g.b0)
            for (key, p) in [(g.a, pa), (g.b, pb)] {
                if var f = fingers[key] {
                    let n = norm(p)
                    f.x = n.x
                    f.y = n.y
                    fingers[key] = f
                    connection.send(["t": "p", "e": "m", "id": f.id, "x": n.x, "y": n.y])
                }
            }
        }
    }

    private func pinchMoved(_ g: (a: ObjectIdentifier, b: ObjectIdentifier, zoom0: CGFloat, d0: CGFloat, q0: CGPoint),
                            _ event: UIEvent?) {
        guard let pos = positions(g.a, g.b, event) else { return }
        let (pa, pb) = pos
        let d = hypot(pa.x - pb.x, pa.y - pb.y)
        let m = CGPoint(x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2)
        let c = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        let z = min(5, max(1, g.zoom0 * d / g.d0))
        // il punto dell'immagine che era tra le dita resta tra le dita
        setZoom(z, CGPoint(x: m.x - c.x - z * (g.q0.x - c.x), y: m.y - c.y - z * (g.q0.y - c.y)), animated: false)
    }

    private func setZoom(_ z: CGFloat, _ p: CGPoint, animated: Bool) {
        zoom = z
        // l'immagine ingrandita copre sempre tutto lo schermo: niente bordi vuoti
        let maxX = (z - 1) * view.bounds.width / 2, maxY = (z - 1) * view.bounds.height / 2
        pan = CGPoint(x: min(maxX, max(-maxX, p.x)), y: min(maxY, max(-maxY, p.y)))
        if animated {
            UIView.animate(withDuration: 0.2) { self.applyZoom() }
        } else {
            applyZoom()
        }
    }

    private func applyZoom() {
        video.transform = CGAffineTransform(translationX: pan.x, y: pan.y).scaledBy(x: zoom, y: zoom)
    }

    // MARK: file trascinati sullo schermo (da File, Foto, Safari…)

    func dropInteraction(_ interaction: UIDropInteraction, canHandle session: UIDropSession) -> Bool {
        session.hasItemsConforming(toTypeIdentifiers: [UTType.item.identifier])
    }

    func dropInteraction(_ interaction: UIDropInteraction, sessionDidUpdate session: UIDropSession) -> UIDropProposal {
        UIDropProposal(operation: .copy)
    }

    func dropInteraction(_ interaction: UIDropInteraction, performDrop session: UIDropSession) {
        let point = session.location(in: view)
        let providers = session.items.map(\.itemProvider)
        showToast(providers.count == 1 ? "Invio al PC…" : "Invio al PC di \(providers.count) elementi…")
        Task { @MainActor in
            var urls: [URL] = []
            for provider in providers {
                if let url = await FilesViewController.copy(provider) { urls.append(url) }
            }
            guard !urls.isEmpty else {
                showToast("Non riesco a leggere quello che hai trascinato")
                return
            }
            Uploader.send(urls, progress: { _ in }, done: { [weak self] result in
                guard let self else { return }
                switch result {
                case .success(let names):
                    guard let first = names.first else { return }
                    // si apre sul PC nel programma su cui l'hai lasciato (gli altri restano nella cartella)
                    let n = self.norm(point)
                    self.connection.send(["t": "deposita", "nome": first, "x": n.x, "y": n.y])
                    self.showToast(names.count == 1 ? "Inviato: apro \(first) sul PC" : "Inviati \(names.count) file: apro \(first)")
                case .failure(let error):
                    self.showToast("Invio non riuscito: \(error.localizedDescription)")
                }
            })
        }
    }

    // MARK: Apple Pencil

    private var penBusy: Bool { penTouch != nil || CACurrentMediaTime() - penUpAt < 0.6 }

    private func penBegan(_ t: UITouch) {
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "d", "b": 0, "x": n.x, "y": n.y])
            penTouch = t
            return
        }
        // il palmo era già appoggiato: annullo quei tocchi prima che la penna scriva
        for f in fingers.values { connection.send(["t": "p", "e": "c", "id": f.id, "x": f.x, "y": f.y]) }
        fingers.removeAll()
        penTouch = t
        penFinger = false
        tapStarts[ObjectIdentifier(t)] = (CACurrentMediaTime(), t.location(in: view))
        sendPen("d", t)
        lastPenPoint = t.preciseLocation(in: view)
    }

    private func penMoved(_ t: UITouch, _ event: UIEvent?) {
        guard t === penTouch else { return }
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "m", "b": 0, "x": n.x, "y": n.y])
            return
        }
        // la Pencil misura fino a 240 punti al secondo: li mando tutti, la linea viene più fluida
        let points = event?.coalescedTouches(for: t) ?? [t]
        let drawInk = Settings.instantInk && !penFinger && (Settings.penMode == "penna" || Settings.penMode == "auto")
        for c in points {
            sendPen("m", c)
            let p = c.preciseLocation(in: view)
            if drawInk, let last = lastPenPoint { ink.add(from: last, to: p, width: 0.8 + pressure(c) * 2.4) }
            lastPenPoint = p
        }
        if drawInk, let last = lastPenPoint {
            let ahead = (event?.predictedTouches(for: t) ?? []).map { $0.preciseLocation(in: view) }
            ink.setPredicted([last] + ahead)
        }
    }

    private func penEnded(_ t: UITouch, cancelled: Bool) {
        guard t === penTouch else { return }
        if Settings.penMode == "mouse" {
            let n = norm(t.preciseLocation(in: view))
            connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
        } else {
            sendPen(cancelled ? "c" : "u", t)
            tapEnded(t, valid: !cancelled && penFinger)  // solo dove la Pencil fa da dito, non mentre disegna
        }
        penTouch = nil
        penUpAt = CACurrentMediaTime()
        lastPenPoint = nil
        ink.endStroke()
    }

    private func pressure(_ t: UITouch) -> CGFloat {
        t.maximumPossibleForce > 0 ? min(1, t.force / t.maximumPossibleForce) : 0.5
    }

    private func sendPen(_ kind: String, _ t: UITouch) {
        let n = norm(t.preciseLocation(in: view))
        // inclinazione: da altezza e direzione della Pencil agli angoli X/Y usati da Windows
        let altitude = Double(t.altitudeAngle)
        let azimuth = Double(t.azimuthAngle(in: view))
        let cot = altitude > 0.01 ? 1 / tan(altitude) : 100
        let tiltX = atan(cos(azimuth) * cot) * 180 / .pi
        let tiltY = atan(sin(azimuth) * cot) * 180 / .pi
        connection.send(["t": "pen", "e": kind, "x": n.x, "y": n.y,
                         "p": kind == "u" ? 0 : Double(pressure(t)),
                         "tx": Int(tiltX), "ty": Int(tiltY), "er": Settings.penMode == "gomma",
                         "auto": Settings.penMode == "auto"])
    }

    // MARK: dita in modalità mouse

    private func mouseBegan(_ t: UITouch) {
        mouseFingers.append(t)
        if mouseFingers.count == 1 {
            dragging = true
            let n = norm(t.location(in: view))
            connection.send(["t": "m", "e": "d", "b": 0, "x": n.x, "y": n.y])
        } else if mouseFingers.count == 2 {
            // secondo dito: niente trascinamento, si passa a scorrimento o clic destro
            if dragging, let first = mouseFingers.first {
                let n = norm(first.location(in: view))
                connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
            }
            dragging = false
            twoStart = CACurrentMediaTime()
            twoMoved = false
            lastMid = midpoint()
        }
    }

    private func mouseMoved(_ t: UITouch) {
        if dragging, mouseFingers.count == 1 {
            let n = norm(t.location(in: view))
            connection.send(["t": "m", "e": "m", "b": 0, "x": n.x, "y": n.y])
        } else if mouseFingers.count == 2, let last = lastMid, let mid = midpoint() {
            let dx = mid.x - last.x, dy = mid.y - last.y
            if abs(dx) + abs(dy) > 2 {
                twoMoved = true
                let n = norm(mid)
                connection.send(["t": "w", "x": n.x, "y": n.y, "dx": Int(dx * 4), "dy": Int(dy * 4)])
                lastMid = mid
            }
        }
    }

    private func mouseEnded(_ t: UITouch) {
        let n = norm(t.location(in: view))
        if dragging && mouseFingers.count == 1 {
            connection.send(["t": "m", "e": "u", "b": 0, "x": n.x, "y": n.y])
            dragging = false
        } else if mouseFingers.count == 2 && !twoMoved && CACurrentMediaTime() - twoStart < 0.35 {
            connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])  // tocco con due dita = clic destro
            twoMoved = true
        }
        mouseFingers.removeAll { $0 === t }
    }

    private func midpoint() -> CGPoint? {
        guard !mouseFingers.isEmpty else { return nil }
        let pts = mouseFingers.map { $0.location(in: view) }
        return CGPoint(x: pts.map(\.x).reduce(0, +) / CGFloat(pts.count), y: pts.map(\.y).reduce(0, +) / CGFloat(pts.count))
    }

    // MARK: menu

    private func menuItems() -> [UIMenuElement] {
        let rtt = connection.rtt.map { "\(Int($0)) ms" } ?? "–"
        let state = UIAction(title: connection.ready ? "Collegato a \(Settings.host) · ritardo \(rtt)" : "Non collegato",
                             image: UIImage(systemName: connection.ready ? "wifi" : "wifi.slash"), attributes: .disabled) { _ in }

        let quality = UIMenu(title: "Qualità", image: UIImage(systemName: "dial.medium"),
                             children: Self.qualityNames.enumerated().map { i, name in
            UIAction(title: name, state: i == Settings.quality ? .on : .off) { [weak self] _ in
                Settings.quality = i
                self?.connection.send(["t": "quality", "level": i])
            }
        })
        let fingersMenu = UIMenu(title: "Dita", image: UIImage(systemName: "hand.point.up"),
                             children: [("touch", "Tocco di Windows"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: name, state: Settings.touchMode == key ? .on : .off) { [weak self] _ in
                self?.releaseInput()
                Settings.touchMode = key
            }
        })
        let pencil = UIMenu(title: "Apple Pencil", image: UIImage(systemName: "pencil.tip"),
                            children: [("auto", "Automatica (scrive o scorre)"), ("penna", "Penna con pressione"),
                                       ("gomma", "Gomma"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: name, state: Settings.penMode == key ? .on : .off) { _ in Settings.penMode = key }
        })
        let ink = UIAction(title: "Inchiostro immediato", image: UIImage(systemName: "scribble"),
                           state: Settings.instantInk ? .on : .off) { _ in Settings.instantInk.toggle() }
        let files = UIAction(title: "File condivisi", image: UIImage(systemName: "folder")) { [weak self] _ in
            self?.showFiles()
        }
        let clipSync = UIAction(title: "Appunti condivisi con il PC", image: UIImage(systemName: "doc.on.clipboard"),
                                state: Settings.clipboardSync ? .on : .off) { _ in
            Settings.clipboardSync.toggle()
            if Settings.clipboardSync { Settings.pasteboardSeen = UIPasteboard.general.changeCount }
        }
        let clipSend = UIAction(title: "Invia appunti al PC", image: UIImage(systemName: "arrow.up.doc.on.clipboard")) { [weak self] _ in
            self?.clipboardToPC(force: true)
        }
        let password = UIAction(title: "Inserisci password…", image: UIImage(systemName: "key.fill")) { [weak self] _ in
            self?.askPassword()
        }
        let typeText = UIAction(title: "Scrivi sul PC…", image: UIImage(systemName: "keyboard")) { [weak self] _ in
            self?.askText()
        }
        let reconnect = UIAction(title: "Ricollega", image: UIImage(systemName: "arrow.clockwise")) { [weak self] _ in
            self?.connection.connect()
        }
        let repair = UIAction(title: "Abbina di nuovo", image: UIImage(systemName: "key")) { [weak self] _ in
            self?.connection.forgetPairing()
        }
        let host = UIAction(title: "Cambia PC…", image: UIImage(systemName: "desktopcomputer")) { [weak self] _ in
            self?.askHost()
        }
        let unzoom = UIAction(title: "Torna al 100%", image: UIImage(systemName: "arrow.down.right.and.arrow.up.left")) { [weak self] _ in
            self?.setZoom(1, .zero, animated: true)
        }
        let extra: [UIMenuElement] = zoom > 1.01 ? [unzoom] : []
        return extra + [state, files, UIMenu(title: "Appunti", image: UIImage(systemName: "doc.on.clipboard"), children: [clipSync, clipSend]),
                UIMenu(options: .displayInline, children: [password, typeText]),
                quality, fingersMenu, pencil, ink,
                UIMenu(options: .displayInline, children: [reconnect, repair, host])]
    }

    private func askHost() {
        let alert = UIAlertController(title: "Collega al PC",
                                      message: "Scrivi l'indirizzo che compare in alto nella finestra di Schermo Esteso sul PC, senza https://. Esempio: 192.168.2.99:8770",
                                      preferredStyle: .alert)
        alert.addTextField { f in
            f.text = Settings.host
            f.placeholder = "192.168.2.99:8770"
            f.keyboardType = .URL
            f.autocorrectionType = .no
            f.autocapitalizationType = .none
        }
        alert.addAction(UIAlertAction(title: "Collega", style: .default) { [weak self, weak alert] _ in
            var text = alert?.textFields?.first?.text ?? ""
            for prefix in ["https://", "http://", "wss://"] where text.lowercased().hasPrefix(prefix) {
                text = String(text.dropFirst(prefix.count))
            }
            Settings.host = text.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
            self?.connection.connect()
        })
        // un alert già aperto (es. il PIN) va chiuso prima
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(alert, animated: true) }
        } else {
            present(alert, animated: true)
        }
    }

    // MARK: file

    func showFiles() {
        if let panel = filesPanel { panel.reload(); return }
        let panel = FilesViewController()
        panel.openOnPC = { [weak self] name in
            self?.connection.send(["t": "apri", "nome": name])
            self?.showToast("Apro \(name) sul PC, su questo schermo…")
        }
        let nav = UINavigationController(rootViewController: panel)
        nav.modalPresentationStyle = .formSheet
        filesPanel = panel
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(nav, animated: true) }
        } else {
            present(nav, animated: true)
        }
    }

    /// File condivisi da un'altra app con "Condividi → Schermo Esteso": vanno subito al PC.
    func receiveShared(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        loadViewIfNeeded()  // può arrivare all'avvio, prima che lo schermo sia pronto
        if Settings.host.isEmpty || Settings.token.isEmpty {
            pendingShared += urls
            showToast("Prima collega l'app al PC: invierò il file subito dopo")
            return
        }
        showToast(urls.count == 1 ? "Invio al PC: \(urls[0].lastPathComponent)…" : "Invio al PC di \(urls.count) file…")
        Uploader.send(urls, progress: { _ in }, done: { [weak self] result in
            for u in urls where u.path.contains("/Inbox/") { try? FileManager.default.removeItem(at: u) }  // copie fatte da iOS
            switch result {
            case .success(let names):
                self?.showToast(names.count == 1 ? "Inviato al PC: \(names[0])" : "Inviati al PC \(names.count) file")
                self?.filesPanel?.reload()
            case .failure(let error):
                self?.showToast("Invio non riuscito: \(error.localizedDescription)")
            }
        })
    }

    // MARK: doppio tocco: seleziona la parola e mostra Copia / Incolla

    private func tapEnded(_ t: UITouch, valid: Bool) {
        guard let start = tapStarts.removeValue(forKey: ObjectIdentifier(t)) else { return }
        let now = CACurrentMediaTime(), p = t.location(in: view)
        guard valid, now - start.time < 0.35, hypot(p.x - start.point.x, p.y - start.point.y) < 12 else {
            lastTap = nil
            return
        }
        if let last = lastTap, now - last.time < 0.45, hypot(p.x - last.point.x, p.y - last.point.y) < 30 {
            lastTap = nil
            selectWord(at: p, delivered: true)  // la Pencil ha già mandato tutti e due i tocchi
        } else {
            lastTap = (now, p)
        }
    }

    /// Il PC seleziona la parola (doppio tocco o doppio clic, secondo il programma) e risponde
    /// "parola_ok": allora compare il fumetto. delivered = i due tocchi sono già arrivati a Windows.
    private func selectWord(at p: CGPoint, delivered: Bool) {
        menuPoint = p
        let n = norm(p)
        connection.send(["t": "parola", "x": n.x, "y": n.y, "gia": delivered])
    }

    private func showEditMenu() {
        editMenu?.presentEditMenu(with: UIEditMenuConfiguration(identifier: nil, sourcePoint: menuPoint))
    }

    /// Dito tenuto fermo mezzo secondo: come sull'iPad compare il fumetto. A Windows non arriva niente,
    /// così la selezione resta com'è.
    private func startLongPress(_ key: ObjectIdentifier) {
        longPress?.invalidate()
        longPress = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: false) { [weak self] _ in
            guard let self, let p = self.pending, p.key == key else { return }
            self.pending = nil
            self.longPressed = key
            self.lastTap = nil
            self.menuPoint = p.point
            self.showEditMenu()
        }
    }

    func editMenuInteraction(_ interaction: UIEditMenuInteraction, menuFor configuration: UIEditMenuConfiguration,
                             suggestedActions: [UIMenuElement]) -> UIMenu? {
        let key = { [weak self] (k: String) in self?.connection.send(["t": "tasto", "k": k]) }
        return UIMenu(children: [
            UIAction(title: "Copia") { _ in key("copia") },
            UIAction(title: "Taglia") { _ in key("taglia") },
            UIAction(title: "Incolla") { [weak self] _ in
                self?.clipboardToPC(force: false)  // prima porto sul PC quello che c'è negli appunti dell'iPad
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { key("incolla") }
            },
            UIAction(title: "Seleziona tutto") { _ in key("tutto") },
            UIAction(title: "Altro (clic destro)") { [weak self] _ in
                guard let self else { return }
                let n = self.norm(self.menuPoint)
                self.connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])
            },
        ])
    }

    // MARK: scrivere sul PC (anche password dal Portachiavi dell'iPad)

    /// Campo nativo dell'iPad: sopra la tastiera compare la chiave delle password salvate (Face ID).
    /// La password va solo nel campo selezionato sul PC: niente appunti, niente console.
    private func askPassword() {
        let alert = UIAlertController(title: "Inserisci password",
                                      message: "Tocca prima sul PC il campo della password (o del nome utente). Poi scegli qui la password salvata: la tastiera mostra la chiave delle Password.",
                                      preferredStyle: .alert)
        alert.addTextField { f in
            f.placeholder = "Nome utente (facoltativo)"
            f.textContentType = .username
            f.autocapitalizationType = .none
            f.autocorrectionType = .no
        }
        alert.addTextField { f in
            f.placeholder = "Password"
            f.textContentType = .password
            f.isSecureTextEntry = true
        }
        let type = { [weak self, weak alert] (enter: Bool) in
            let user = alert?.textFields?[0].text ?? ""
            let pass = alert?.textFields?[1].text ?? ""
            guard let self, !(user.isEmpty && pass.isEmpty) else { return }
            if !user.isEmpty {
                self.connection.send(["t": "scrivi", "testo": user])
                if !pass.isEmpty { self.connection.send(["t": "tasto", "k": "tab"]) }
            }
            if !pass.isEmpty { self.connection.send(["t": "scrivi", "testo": pass, "segreto": true]) }
            if enter { self.connection.send(["t": "tasto", "k": "invio"]) }
            alert?.textFields?.forEach { $0.text = "" }
        }
        alert.addAction(UIAlertAction(title: "Annulla", style: .cancel))
        alert.addAction(UIAlertAction(title: "Inserisci", style: .default) { _ in type(false) })
        alert.addAction(UIAlertAction(title: "Inserisci e premi Invio", style: .default) { _ in type(true) })
        presentReplacing(alert)
    }

    private func askText() {
        let alert = UIAlertController(title: "Scrivi sul PC",
                                      message: "Il testo viene scritto nel punto selezionato sul PC, come con una tastiera.",
                                      preferredStyle: .alert)
        alert.addTextField { f in f.placeholder = "Testo" }
        let type = { [weak self, weak alert] (enter: Bool) in
            let text = alert?.textFields?.first?.text ?? ""
            guard let self else { return }
            if !text.isEmpty { self.connection.send(["t": "scrivi", "testo": text]) }
            if enter { self.connection.send(["t": "tasto", "k": "invio"]) }
        }
        alert.addAction(UIAlertAction(title: "Annulla", style: .cancel))
        alert.addAction(UIAlertAction(title: "Scrivi", style: .default) { _ in type(false) })
        alert.addAction(UIAlertAction(title: "Scrivi e premi Invio", style: .default) { _ in type(true) })
        presentReplacing(alert)
    }

    private func presentReplacing(_ vc: UIViewController) {
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(vc, animated: true) }
        } else {
            present(vc, animated: true)
        }
    }

    // MARK: appunti

    /// Testo o immagine copiati sul PC: vanno direttamente negli appunti dell'iPad.
    private func clipboardFromPC(_ m: [String: Any]) {
        guard Settings.clipboardSync else { return }
        if m["tipo"] as? String == "testo", let text = m["testo"] as? String {
            UIPasteboard.general.string = text
            Settings.pasteboardSeen = UIPasteboard.general.changeCount  // non lo rimando indietro
            showToast("Appunti dal PC copiati su \(UIDevice.current.model)")
        } else if m["tipo"] as? String == "immagine" {
            Task { @MainActor in
                guard let data = try? await FileAPI.clipboardImage(), let image = UIImage(data: data) else { return }
                UIPasteboard.general.image = image
                Settings.pasteboardSeen = UIPasteboard.general.changeCount
                showToast("Immagine dagli appunti del PC copiata su \(UIDevice.current.model)")
            }
        }
    }

    /// Appunti dell'iPad -> PC. In automatico solo se sono cambiati da quando li ho visti l'ultima volta
    /// (iPadOS può chiedere "Consentire di incollare?": in Impostazioni → Schermo Esteso si può scegliere Consenti).
    private func clipboardToPC(force: Bool) {
        let pb = UIPasteboard.general
        if Settings.pasteboardSeen < 0 { Settings.pasteboardSeen = pb.changeCount }  // prima volta: niente invii
        guard force || (Settings.clipboardSync && pb.changeCount != Settings.pasteboardSeen) else { return }
        Settings.pasteboardSeen = pb.changeCount
        guard connection.ready else { return }
        if pb.hasStrings, let text = pb.string, !text.isEmpty {
            connection.send(["t": "appunti", "testo": text])
            showToast("Appunti di \(UIDevice.current.model) copiati sul PC")
        } else if pb.hasImages, let png = pb.image?.pngData() {
            Task { @MainActor in
                do {
                    try await FileAPI.sendClipboardImage(png)
                    showToast("Immagine di \(UIDevice.current.model) copiata negli appunti del PC")
                } catch {
                    showToast("Appunti non inviati: \(error.localizedDescription)")
                }
            }
        } else if force {
            showToast("Gli appunti di \(UIDevice.current.model) sono vuoti")
        }
    }

    // MARK: messaggi a schermo

    private func showStatus(_ text: String) {
        statusLabel.text = text
        statusLabel.isHidden = false
        spinner.startAnimating()
    }

    private func hideStatus() {
        statusLabel.isHidden = true
        spinner.stopAnimating()
    }

    private func showToast(_ text: String) {
        toast.text = "   \(text)   "
        UIView.animate(withDuration: 0.2) { self.toast.alpha = 1 }
        UIView.animate(withDuration: 0.4, delay: 6, options: []) { self.toast.alpha = 0 }
    }
}
