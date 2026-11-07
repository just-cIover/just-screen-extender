import AVFoundation
import UIKit
import UniformTypeIdentifiers

/// The PC video: decoded and drawn by the iPad hardware.
final class VideoView: UIView {
    override class var layerClass: AnyClass { AVSampleBufferDisplayLayer.self }
    var display: AVSampleBufferDisplayLayer { layer as! AVSampleBufferDisplayLayer }
}

/// Instant ink: the Pencil stroke appears right away and fades when the real one arrives
/// from the PC. It also draws the points "predicted" by iPadOS, which anticipate the tip movement.
final class InkView: UIView {
    private struct Segment { let a: CGPoint; let b: CGPoint; let width: CGFloat; let time: CFTimeInterval }
    private var segments: [Segment] = []
    private var predicted: [CGPoint] = []
    private var link: CADisplayLink?
    var lifetime: () -> Double = { 0.25 }  // seconds it stays visible (about the connection delay)

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

    /// Removes all the ink right away (the Pencil is scrolling, not writing).
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
    private var pendingShared: [URL] = []  // files that arrived from other apps before the PC was configured
    private static let qualityNames = ["Massima (60 fps)", "Fluida (30 fps)", "Media (75%)", "Leggera (50%)"]

    // touch
    private var fingers: [ObjectIdentifier: (id: Int, x: Double, y: Double)] = [:]
    private var nextTouchId = 1
    // Pencil
    private var penTouch: UITouch?
    private var penUpAt: CFTimeInterval = 0
    private var lastPenPoint: CGPoint?
    private var penFinger = false  // automatic mode: in this stroke the Pencil acts as a finger (no ink)
    // double tap = select the word (as on the iPad)
    private var tapStarts: [ObjectIdentifier: (time: CFTimeInterval, point: CGPoint)] = [:]
    private var lastTap: (time: CFTimeInterval, point: CGPoint)?
    private var editMenu: UIEditMenuInteraction?
    // a single finger just put down: it doesn't go to Windows until we know whether it's a tap,
    // a drag or a long press (so holding it down doesn't touch anything on the PC)
    private var pending: (key: ObjectIdentifier, point: CGPoint, time: CFTimeInterval)?
    private var longPress: Timer?
    private var longPressed: ObjectIdentifier?  // finger held down: the callout is open, I ignore the rest
    private var menuPoint = CGPoint.zero        // where to show the callout (and where to right-click)
    // two-finger zoom: only the picture on the device, Windows doesn't change
    private var zoom: CGFloat = 1
    private var pan = CGPoint.zero
    private var two: (a: ObjectIdentifier, b: ObjectIdentifier, a0: CGPoint, b0: CGPoint)?  // pinch or scroll?
    private var pinching: (a: ObjectIdentifier, b: ObjectIdentifier, zoom0: CGFloat, d0: CGFloat, q0: CGPoint)?
    private var ignored = Set<ObjectIdentifier>()  // fingers of a gesture that has already ended (or third fingers)
    // mouse mode with the fingers
    private var mouseFingers: [UITouch] = []
    private var dragging = false
    private var twoStart: CFTimeInterval = 0
    private var twoMoved = false
    private var lastMid: CGPoint?

    override func loadView() { view = UIView() }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        // the video sits in a container: so I can zoom it without zooming ink, menu and notices
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
        NotificationCenter.default.addObserver(forName: Lingua.changed, object: nil, queue: .main) { [weak self] _ in
            guard let self, let text = self.statusText, !self.statusLabel.isHidden else { return }
            self.statusLabel.text = L(text)
        }
        connection.onWhere = { [weak self] side, secs in self?.askWhere(side, seconds: secs) }
        connection.onWhereDone = { [weak self] in self?.whereAlert?.dismiss(animated: true) }
        connection.onRest = { [weak self] resting in
            // PC asleep or gone: the device can lock by itself as usual; when it's back it stays on again.
            // The connection decides the messages
            UIApplication.shared.isIdleTimerDisabled = !resting && self?.connection.ready == true
        }
        connection.onPenUse = { [weak self] use in
            // the PC decided: there's no drawing program under the tip, so it scrolls and taps
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
        // moves when dragged, like the Pencil toolbar in Notes: when released it snaps to the nearest spot
        let drag = UIPanGestureRecognizer(target: self, action: #selector(dragMenu(_:)))
        drag.delegate = self
        menuButton.addGestureRecognizer(drag)

        for v in [statusLabel, spinner, toast] as [UIView] {
            v.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(v)
        }
        view.addSubview(menuButton)  // positioned by hand (placeMenu), not with constraints
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
        ])
        showStatus("Avvio…")
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        video.bounds = view.bounds
        video.center = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        applyZoom()
        if !menuDragging { placeMenu(animated: false) }
    }

    // MARK: movable menu button

    private var menuDragging = false
    private var menuGrab = CGPoint.zero  // where the finger grabbed the button, relative to its center

    /// The spots where the button can snap: 0 top-left, 1 top-center, 2 top-right, 3 middle-right,
    /// 4 bottom-right, 5 bottom-center, 6 bottom-left, 7 middle-left (inside the safe area, 6 pt from the edge).
    private func menuSpots() -> [CGPoint] {
        let r = view.bounds.inset(by: view.safeAreaInsets).insetBy(dx: 6 + 22, dy: 6 + 22)
        return [CGPoint(x: r.minX, y: r.minY), CGPoint(x: r.midX, y: r.minY), CGPoint(x: r.maxX, y: r.minY),
                CGPoint(x: r.maxX, y: r.midY), CGPoint(x: r.maxX, y: r.maxY), CGPoint(x: r.midX, y: r.maxY),
                CGPoint(x: r.minX, y: r.maxY), CGPoint(x: r.minX, y: r.midY)]
    }

    private func placeMenu(animated: Bool) {
        let spots = menuSpots()
        let spot = spots[min(max(Settings.menuSpot, 0), spots.count - 1)]
        let set = { self.menuButton.bounds.size = CGSize(width: 44, height: 44); self.menuButton.center = spot }
        if animated {
            UIView.animate(withDuration: 0.3, delay: 0, usingSpringWithDamping: 0.8, initialSpringVelocity: 0.5,
                           options: [.allowUserInteraction], animations: set)
        } else {
            set()
        }
    }

    @objc private func dragMenu(_ g: UIPanGestureRecognizer) {
        let p = g.location(in: view)
        switch g.state {
        case .began:
            // the menu opens on tap: if the finger drags instead, I close it and move the button
            menuButton.contextMenuInteraction?.dismissMenu()
            menuDragging = true
            menuGrab = CGPoint(x: p.x - menuButton.center.x, y: p.y - menuButton.center.y)
            UIView.animate(withDuration: 0.15) { self.menuButton.transform = CGAffineTransform(scaleX: 1.15, y: 1.15) }
        case .changed:
            let b = view.bounds.insetBy(dx: 22, dy: 22)
            menuButton.center = CGPoint(x: min(max(p.x - menuGrab.x, b.minX), b.maxX),
                                        y: min(max(p.y - menuGrab.y, b.minY), b.maxY))
        default:
            menuDragging = false
            // snapped to the nearest spot (taking the throw momentum into account, as in Notes)
            let v = g.velocity(in: view)
            let aim = CGPoint(x: menuButton.center.x + v.x * 0.15, y: menuButton.center.y + v.y * 0.15)
            let spots = menuSpots()
            let best = spots.indices.min { hypot(spots[$0].x - aim.x, spots[$0].y - aim.y) < hypot(spots[$1].x - aim.x, spots[$1].y - aim.y) } ?? 2
            Settings.menuSpot = best
            UIView.animate(withDuration: 0.15) { self.menuButton.transform = .identity }
            placeMenu(animated: true)
        }
    }

    // real full screen: no iPadOS bar, Home indicator hidden, less sensitive edges
    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var preferredScreenEdgesDeferringSystemGestures: UIRectEdge { .all }

    // MARK: lifecycle (called by the scene)

    func resume() {
        guard !active else { return }
        active = true
        if connection.ready { clipboardToPC(force: false) }
        if Settings.host.isEmpty {
            askHost()
        } else {
            connection.resetOffline()  // reopened by the user: retry right away, not every 10 s
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
        let text = (message.map { L($0) + "\n\n" } ?? "") + L("Questo \(UIDevice.current.model) è nuovo per il PC: inserisci il PIN di 6 cifre che compare ora nella finestra di Screen Extender.")
        if let alert = pinAlert {
            alert.message = text
            alert.textFields?.first?.text = ""
            return
        }
        let alert = UIAlertController(title: L("Abbinamento"), message: text, preferredStyle: .alert)
        alert.addTextField { f in
            f.keyboardType = .numberPad
            f.textContentType = .oneTimeCode
            f.placeholder = "PIN"
            f.textAlignment = .center
        }
        alert.addAction(UIAlertAction(title: L("Cambia PC"), style: .default) { [weak self] _ in self?.askHost() })
        alert.addAction(UIAlertAction(title: L("Collega"), style: .default) { [weak self, weak alert] _ in
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
        UIApplication.shared.isIdleTimerDisabled = true  // the device doesn't go to sleep while it's acting as a screen
        let info = Bundle.main.infoDictionary
        let version = "\(info?["CFBundleShortVersionString"] as? String ?? "?") (\(info?["CFBundleVersion"] as? String ?? "?"))"  // e.g. 1.1.0 (23): version and build
        let device = UIDevice.current
        c.send(["t": "diag", "motore": "nativo", "avvio": true,
                "ua": "Screen Extender iOS \(version), \(device.model) \(device.systemName) \(device.systemVersion)"])
        lastArea = []  // new connection: send the sizes again anyway
        sendArea()
    }

    /// Usable pixels: the whole screen, except the iPhone notch or Dynamic Island (on the sides in landscape,
    /// at the top in portrait). The virtual screen takes exactly these sizes, also when rotated to portrait.
    private var lastArea: [Int] = []

    private func sendArea() {
        guard connection.ready else { return }
        let b = view.bounds, i = view.safeAreaInsets, scale = UIScreen.main.nativeScale
        let w = (b.width - i.left - i.right) * scale, h = (b.height - i.top) * scale
        let even = { (v: CGFloat) in Int(v.rounded()) & ~1 }  // video encoding wants even sizes
        let area = [even(w), even(h)]
        guard area != lastArea else { return }
        lastArea = area
        connection.send(["t": "area", "w": area[0], "h": area[1]])
    }

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask {
        guard Settings.rotate else { return .landscape }
        return UIDevice.current.userInterfaceIdiom == .pad ? .all : .allButUpsideDown
    }

    override func viewWillTransition(to size: CGSize, with coordinator: UIViewControllerTransitionCoordinator) {
        super.viewWillTransition(to: size, with: coordinator)
        coordinator.animate(alongsideTransition: nil) { [weak self] _ in
            self?.sendArea()  // rotated: the PC rebuilds the virtual screen with the new sizes
        }
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
        if display.status == .failed {  // the decoder stopped: restart from the next full frame
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
        showToast(names.count == 1 ? L("Nuovo file da \(sender): \(names[0])") : L("\(names.count) nuovi file da \(sender)"))
        filesPanel?.reload()
    }

    // MARK: statistics for the PC window

    /// The app isn't in the foreground (Control Center, app switcher, notification): frames and delay from
    /// these seconds say nothing about the network, so the PC must not use them for automatic quality.
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
        let device = UIDevice.current
        device.isBatteryMonitoringEnabled = true
        let level = device.batteryLevel  // -1 if unknown (e.g. simulator)
        let charging = device.batteryState == .charging || device.batteryState == .full
        connection.send(["t": "stats", "fps": framesShown / 2, "dec": framesShown / 2,
                         "batt": level >= 0 ? Int((level * 100).rounded()) as Any : NSNull(), "carica": charging,
                         "rtt": connection.rtt.map { Int($0) as Any } ?? NSNull(),  // JSON: null if not measured
                         "mbit": Double(bytesReceived * 8) / 2e6, "render": "nativo/hardware",
                         "q": ["Massima", "Fluida", "Media", "Leggera"][max(0, min(q, 3))]])
        framesShown = 0
        bytesReceived = 0
    }

    // MARK: coordinates

    private var contentRect: CGRect { AVMakeRect(aspectRatio: videoSize, insideRect: video.bounds) }

    private func norm(_ p: CGPoint) -> (x: Double, y: Double) {
        let q = video.convert(p, from: view)  // takes the zoom into account
        let r = contentRect
        guard r.width > 0, r.height > 0 else { return (0, 0) }
        return (Double((q.x - r.minX) / r.width), Double((q.y - r.minY) / r.height))
    }

    // MARK: touches

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        editMenu?.dismissMenu()
        let fingersOnly = touches.allSatisfy { $0.type != .pencil }
        if fingersOnly && Settings.touchMode != "mouse" && !penBusy && touches.count == 2 && fingers.isEmpty
            && pending == nil && two == nil && pinching == nil {
            // two fingers put down together: wait to see whether it's a pinch (zoom) or a scroll
            let t = Array(touches)
            two = (ObjectIdentifier(t[0]), ObjectIdentifier(t[1]), t[0].location(in: view), t[1].location(in: view))
            return
        }
        for t in touches {
            let key = ObjectIdentifier(t)
            if t.type == .pencil {
                penBegan(t)
            } else if penBusy {
                continue  // palm resting while writing with the Pencil
            } else if Settings.touchMode == "mouse" {
                mouseBegan(t)
            } else if two != nil || pinching != nil {
                ignored.insert(key)  // third finger during a pinch
            } else if let p = pending, fingers.isEmpty {
                // second finger while the first is waiting: pinch (zoom here) or two-finger scroll (Windows)?
                longPress?.invalidate()
                pending = nil
                two = (p.key, key, p.point, t.location(in: view))
            } else if fingers.isEmpty && pending == nil && touches.count == 1 {
                pending = (key, t.location(in: view), CACurrentMediaTime())
                startLongPress(key)
            } else {
                flushPending()  // second finger (zoom, two-finger scroll): the first one starts right away
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

    /// The waiting finger becomes a touch on Windows, from the point where it was put down.
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
                flushPending()  // it moves: scroll or drag
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
                // two fingers lifted without moving: right click, like the two-finger tap on a trackpad
                two = nil
                ignored.insert(key == g.a ? g.b : g.a)
                if !cancelled {
                    let n = norm(CGPoint(x: (g.a0.x + g.b0.x) / 2, y: (g.a0.y + g.b0.y) / 2))
                    connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])
                }
            } else if let g = pinching, key == g.a || key == g.b {
                pinching = nil
                ignored.insert(key == g.a ? g.b : g.a)
                if zoom < 1.08 { setZoom(1, CGPoint.zero, animated: true) }  // almost at 100%: set it back exactly
            } else if let p = pending, p.key == key {
                pending = nil
                longPress?.invalidate()
                guard !cancelled else { continue }
                let now = CACurrentMediaTime(), end = t.location(in: view)
                let quick = now - p.time < 0.35
                if quick, let last = lastTap, now - last.time < 0.5,
                   hypot(end.x - last.point.x, end.y - last.point.y) < 30 {
                    // second tap of a double tap: not sent, the PC decides how to select
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

    // MARK: two-finger zoom (on the device only)

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
            // the fingers spread or pinch (or the picture is already zoomed): zoom here
            two = nil
            let c = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
            let q0 = CGPoint(x: c.x + (m.x - c.x - pan.x) / zoom, y: c.y + (m.y - c.y - pan.y) / zoom)
            pinching = (g.a, g.b, zoom, max(d, 1), q0)
        } else if hypot(m.x - m0.x, m.y - m0.y) > 12 {
            // they move together: Windows two-finger scroll, from the points where they were put down
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
        // the point of the picture that was between the fingers stays between the fingers
        setZoom(z, CGPoint(x: m.x - c.x - z * (g.q0.x - c.x), y: m.y - c.y - z * (g.q0.y - c.y)), animated: false)
    }

    private func setZoom(_ z: CGFloat, _ p: CGPoint, animated: Bool) {
        zoom = z
        // the zoomed picture always covers the whole screen: no empty borders
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

    // MARK: files dragged onto the screen (from Files, Photos, Safari…)

    func dropInteraction(_ interaction: UIDropInteraction, canHandle session: UIDropSession) -> Bool {
        session.hasItemsConforming(toTypeIdentifiers: [UTType.item.identifier])
    }

    func dropInteraction(_ interaction: UIDropInteraction, sessionDidUpdate session: UIDropSession) -> UIDropProposal {
        UIDropProposal(operation: .copy)
    }

    func dropInteraction(_ interaction: UIDropInteraction, performDrop session: UIDropSession) {
        let point = session.location(in: view)
        let providers = session.items.map(\.itemProvider)
        showToast(providers.count == 1 ? L("Invio al PC…") : L("Invio al PC di \(providers.count) elementi…"))
        Task { @MainActor in
            var urls: [URL] = []
            for provider in providers {
                if let url = await FilesViewController.copy(provider) { urls.append(url) }
            }
            guard !urls.isEmpty else {
                showToast("Non riesco a leggere quello che hai trascinato")
                return
            }
            self.uploadBanner.attach(to: self.view)
            self.uploadBanner.start(urls.count == 1 ? L("Invio al PC: \(urls[0].lastPathComponent)") : L("Invio al PC di \(urls.count) file"), files: urls)
            Uploader.send(urls, progress: { [weak self] p in self?.uploadBanner.update(p) }, done: { [weak self] result in
                guard let self else { return }
                switch result {
                case .success(let names):
                    self.uploadBanner.finish(names.count == 1 ? L("Inviato al PC") : L("Inviati \(names.count) file"), ok: true)
                    guard let first = names.first else { return }
                    // it opens on the PC in the program you dropped it on (the others stay in the folder)
                    let n = self.norm(point)
                    self.connection.send(["t": "deposita", "nome": first, "x": n.x, "y": n.y])
                    self.showToast(names.count == 1 ? L("Inviato: apro \(first) sul PC") : L("Inviati \(names.count) file: apro \(first)"))
                case .failure(let error):
                    self.uploadBanner.finish("Invio non riuscito: \(error.localizedDescription)", ok: false)
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
        // the palm was already resting: cancel those touches before the pen writes
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
        // the Pencil measures up to 240 points per second: send them all, the line comes out smoother
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
            tapEnded(t, valid: !cancelled && penFinger)  // only where the Pencil acts as a finger, not while drawing
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
        // tilt: from the Pencil altitude and azimuth to the X/Y angles used by Windows
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

    // MARK: fingers in mouse mode

    private func mouseBegan(_ t: UITouch) {
        mouseFingers.append(t)
        if mouseFingers.count == 1 {
            dragging = true
            let n = norm(t.location(in: view))
            connection.send(["t": "m", "e": "d", "b": 0, "x": n.x, "y": n.y])
        } else if mouseFingers.count == 2 {
            // second finger: no drag, switch to scroll or right click
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
            connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])  // two-finger tap = right click
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
        let state = UIAction(title: connection.ready ? L("Collegato a \(Settings.host) · ritardo \(rtt)") : L("Non collegato"),
                             image: UIImage(systemName: connection.ready ? "wifi" : "wifi.slash"), attributes: .disabled) { _ in }

        let quality = UIMenu(title: L("Qualità"), image: UIImage(systemName: "dial.medium"),
                             children: Self.qualityNames.enumerated().map { i, name in
            UIAction(title: L(name), state: i == Settings.quality ? .on : .off) { [weak self] _ in
                Settings.quality = i
                self?.connection.send(["t": "quality", "level": i])
            }
        })
        let fingersMenu = UIMenu(title: L("Dita"), image: UIImage(systemName: "hand.point.up"),
                             children: [("touch", "Tocco di Windows"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: L(name), state: Settings.touchMode == key ? .on : .off) { [weak self] _ in
                self?.releaseInput()
                Settings.touchMode = key
            }
        })
        let pencil = UIMenu(title: L("Apple Pencil"), image: UIImage(systemName: "pencil.tip"),
                            children: [("auto", "Automatica (scrive o scorre)"), ("penna", "Penna con pressione"),
                                       ("gomma", "Gomma"), ("mouse", "Mouse")].map { key, name in
            UIAction(title: L(name), state: Settings.penMode == key ? .on : .off) { _ in Settings.penMode = key }
        })
        let rotate = UIAction(title: L("Ruota con il dispositivo"), image: UIImage(systemName: "rectangle.portrait.rotate"),
                              state: Settings.rotate ? .on : .off) { [weak self] _ in
            Settings.rotate.toggle()
            self?.setNeedsUpdateOfSupportedInterfaceOrientations()
        }
        let ink = UIAction(title: L("Inchiostro immediato"), image: UIImage(systemName: "scribble"),
                           state: Settings.instantInk ? .on : .off) { _ in Settings.instantInk.toggle() }
        let files = UIAction(title: L("File condivisi"), image: UIImage(systemName: "folder")) { [weak self] _ in
            self?.showFiles()
        }
        let clipSync = UIAction(title: L("Appunti condivisi con il PC"), image: UIImage(systemName: "doc.on.clipboard"),
                                state: Settings.clipboardSync ? .on : .off) { _ in
            Settings.clipboardSync.toggle()
            if Settings.clipboardSync { Settings.pasteboardSeen = UIPasteboard.general.changeCount }
        }
        let clipSend = UIAction(title: L("Invia appunti al PC"), image: UIImage(systemName: "arrow.up.doc.on.clipboard")) { [weak self] _ in
            self?.clipboardToPC(force: true)
        }
        let password = UIAction(title: L("Inserisci password…"), image: UIImage(systemName: "key.fill")) { [weak self] _ in
            self?.askPassword()
        }
        let typeText = UIAction(title: L("Scrivi sul PC…"), image: UIImage(systemName: "keyboard")) { [weak self] _ in
            self?.askText()
        }
        let reconnect = UIAction(title: L("Ricollega"), image: UIImage(systemName: "arrow.clockwise")) { [weak self] _ in
            self?.connection.connect()
        }
        let repair = UIAction(title: L("Abbina di nuovo"), image: UIImage(systemName: "key")) { [weak self] _ in
            self?.connection.forgetPairing()
        }
        let host = UIAction(title: L("Cambia PC…"), image: UIImage(systemName: "desktopcomputer")) { [weak self] _ in
            self?.askHost()
        }
        let unzoom = UIAction(title: L("Torna al 100%"), image: UIImage(systemName: "arrow.down.right.and.arrow.up.left")) { [weak self] _ in
            self?.setZoom(1, .zero, animated: true)
        }
        let language = UIMenu(title: L("Lingua"), image: UIImage(systemName: "globe"),
                              children: [("it", "🇮🇹 IT · Italiano"), ("en", "🇬🇧 GB · English")].map { code, name in
            UIAction(title: name, state: Lingua.current == code ? .on : .off) { [weak self] _ in
                Lingua.use(code)
                self?.connection.send(["t": "lingua", "lingua": code])
            }
        })
        let extra: [UIMenuElement] = zoom > 1.01 ? [unzoom] : []
        return extra + [state, files, UIMenu(title: L("Appunti"), image: UIImage(systemName: "doc.on.clipboard"), children: [clipSync, clipSend]),
                UIMenu(options: .displayInline, children: [password, typeText]),
                quality, fingersMenu, pencil, ink, rotate, language,
                UIMenu(options: .displayInline, children: [reconnect, repair, host])]
    }

    private func askHost() {
        let alert = UIAlertController(title: L("Collega al PC"),
                                      message: L("Scrivi l'indirizzo che compare in alto nella finestra di Screen Extender sul PC, senza https://. Esempio: 192.168.1.20:8770"),
                                      preferredStyle: .alert)
        alert.addTextField { f in
            f.text = Settings.host
            f.placeholder = "192.168.1.20:8770"
            f.keyboardType = .URL
            f.autocorrectionType = .no
            f.autocapitalizationType = .none
        }
        alert.addAction(UIAlertAction(title: L("Collega"), style: .default) { [weak self, weak alert] _ in
            var text = alert?.textFields?.first?.text ?? ""
            for prefix in ["https://", "http://", "wss://"] where text.lowercased().hasPrefix(prefix) {
                text = String(text.dropFirst(prefix.count))
            }
            Settings.host = text.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
            self?.connection.connect()
        })
        // an alert that is already open (e.g. the PIN) must be closed first
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(alert, animated: true) }
        } else {
            present(alert, animated: true)
        }
    }

    // MARK: files

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

    /// Files shared from another app with "Share → Screen Extender": they go straight to the PC.
    func receiveShared(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        loadViewIfNeeded()  // may arrive at startup, before the screen is ready
        if Settings.host.isEmpty || Settings.token.isEmpty {
            pendingShared += urls
            showToast("Prima collega l'app al PC: invierò il file subito dopo")
            return
        }
        uploadBanner.attach(to: view)
        uploadBanner.start(urls.count == 1 ? L("Invio al PC: \(urls[0].lastPathComponent)") : L("Invio al PC di \(urls.count) file"), files: urls)
        Uploader.send(urls, progress: { [weak self] p in self?.uploadBanner.update(p) }, done: { [weak self] result in
            for u in urls where u.path.contains("/Inbox/") { try? FileManager.default.removeItem(at: u) }  // copies made by iOS
            switch result {
            case .success(let names):
                self?.uploadBanner.finish(names.count == 1 ? L("Inviato al PC: \(names[0])") : L("Inviati al PC \(names.count) file"), ok: true)
                self?.filesPanel?.reload()
            case .failure(let error):
                self?.uploadBanner.finish("Invio non riuscito: \(error.localizedDescription)", ok: false)
            }
        })
    }

    // MARK: double tap: select the word and show Copy / Paste

    private func tapEnded(_ t: UITouch, valid: Bool) {
        guard let start = tapStarts.removeValue(forKey: ObjectIdentifier(t)) else { return }
        let now = CACurrentMediaTime(), p = t.location(in: view)
        guard valid, now - start.time < 0.35, hypot(p.x - start.point.x, p.y - start.point.y) < 12 else {
            lastTap = nil
            return
        }
        if let last = lastTap, now - last.time < 0.45, hypot(p.x - last.point.x, p.y - last.point.y) < 30 {
            lastTap = nil
            selectWord(at: p, delivered: true)  // the Pencil has already sent both taps
        } else {
            lastTap = (now, p)
        }
    }

    /// The PC selects the word (double tap or double click, depending on the program) and answers
    /// "parola_ok": then the callout appears. delivered = both taps have already reached Windows.
    private func selectWord(at p: CGPoint, delivered: Bool) {
        menuPoint = p
        let n = norm(p)
        connection.send(["t": "parola", "x": n.x, "y": n.y, "gia": delivered])
    }

    private func showEditMenu() {
        editMenu?.presentEditMenu(with: UIEditMenuConfiguration(identifier: nil, sourcePoint: menuPoint))
    }

    /// Finger held still for half a second: the callout appears, as on the iPad. Nothing reaches Windows,
    /// so the selection stays as it is.
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
            UIAction(title: L("Copia")) { _ in key("copia") },
            UIAction(title: L("Taglia")) { _ in key("taglia") },
            UIAction(title: L("Incolla")) { [weak self] _ in
                self?.clipboardToPC(force: false)  // first bring the iPad clipboard over to the PC
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { key("incolla") }
            },
            UIAction(title: L("Seleziona tutto")) { _ in key("tutto") },
            UIAction(title: L("Altro (clic destro)")) { [weak self] _ in
                guard let self else { return }
                let n = self.norm(self.menuPoint)
                self.connection.send(["t": "m", "e": "click", "b": 2, "x": n.x, "y": n.y])
            },
        ])
    }

    // MARK: typing on the PC (passwords from the iPad Keychain too)

    /// Native iPad field: the saved-passwords key appears above the keyboard (Face ID).
    /// The password goes only into the selected field on the PC: no clipboard, no console.
    private func askPassword() {
        let alert = UIAlertController(title: L("Inserisci password"),
                                      message: L("Tocca prima sul PC il campo della password (o del nome utente). Poi scegli qui la password salvata: la tastiera mostra la chiave delle Password."),
                                      preferredStyle: .alert)
        alert.addTextField { f in
            f.placeholder = L("Nome utente (facoltativo)")
            f.textContentType = .username
            f.autocapitalizationType = .none
            f.autocorrectionType = .no
        }
        alert.addTextField { f in
            f.placeholder = L("Password")
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
        alert.addAction(UIAlertAction(title: L("Annulla"), style: .cancel))
        alert.addAction(UIAlertAction(title: L("Inserisci"), style: .default) { _ in type(false) })
        alert.addAction(UIAlertAction(title: L("Inserisci e premi Invio"), style: .default) { _ in type(true) })
        presentReplacing(alert)
    }

    private func askText() {
        let alert = UIAlertController(title: L("Scrivi sul PC"),
                                      message: L("Il testo viene scritto nel punto selezionato sul PC, come con una tastiera."),
                                      preferredStyle: .alert)
        alert.addTextField { f in f.placeholder = L("Testo") }
        let type = { [weak self, weak alert] (enter: Bool) in
            let text = alert?.textFields?.first?.text ?? ""
            guard let self else { return }
            if !text.isEmpty { self.connection.send(["t": "scrivi", "testo": text]) }
            if enter { self.connection.send(["t": "tasto", "k": "invio"]) }
        }
        alert.addAction(UIAlertAction(title: L("Annulla"), style: .cancel))
        alert.addAction(UIAlertAction(title: L("Scrivi"), style: .default) { _ in type(false) })
        alert.addAction(UIAlertAction(title: L("Scrivi e premi Invio"), style: .default) { _ in type(true) })
        presentReplacing(alert)
    }

    private func presentReplacing(_ vc: UIViewController) {
        if let shown = presentedViewController {
            shown.dismiss(animated: false) { [weak self] in self?.present(vc, animated: true) }
        } else {
            present(vc, animated: true)
        }
    }

    // MARK: clipboard

    /// Text or image copied on the PC: it goes straight into the iPad clipboard.
    private func clipboardFromPC(_ m: [String: Any]) {
        guard Settings.clipboardSync else { return }
        if m["tipo"] as? String == "testo", let text = m["testo"] as? String {
            UIPasteboard.general.string = text
            Settings.pasteboardSeen = UIPasteboard.general.changeCount  // don't send it back
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

    /// iPad clipboard -> PC. Automatically only if it changed since I last saw it
    /// (iPadOS may ask "Allow Paste?": in Settings → Screen Extender you can choose Allow).
    private func clipboardToPC(force: Bool) {
        let pb = UIPasteboard.general
        if Settings.pasteboardSeen < 0 { Settings.pasteboardSeen = pb.changeCount }  // first time: nothing sent
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

    // MARK: on-screen messages

    private var statusText: String?   // status in Italian, to translate it again if the language changes

    private func showStatus(_ text: String) {
        statusText = text
        statusLabel.text = L(text)
        statusLabel.isHidden = false
        spinner.startAnimating()
    }

    private func hideStatus() {
        statusLabel.isHidden = true
        spinner.stopAnimating()
    }

    /// Mouse push with several devices connected: you tap "Here" on the one that sits on that side of the PC.
    private weak var whereAlert: UIAlertController?
    private let uploadBanner = UploadBanner()  // progress of the files sent to the PC

    private func askWhere(_ side: String, seconds: Int) {
        whereAlert?.dismiss(animated: false)
        let alert = UIAlertController(title: L("Schermo \(side) del PC?"),
                                      message: L("Hai spinto il mouse \(side). Se questo \(UIDevice.current.model) si trova lì, tocca Qui e il suo schermo si sposta da quella parte."),
                                      preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "No", style: .cancel))
        alert.addAction(UIAlertAction(title: L("Qui"), style: .default) { [weak self] _ in
            self?.connection.send(["t": "qui"])
        })
        whereAlert = alert
        present(alert, animated: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + .seconds(seconds)) { [weak alert] in
            alert?.dismiss(animated: true)
        }
    }

    private func showToast(_ text: String) {
        toast.text = "   \(L(text))   "
        UIView.animate(withDuration: 0.2) { self.toast.alpha = 1 }
        UIView.animate(withDuration: 0.4, delay: 6, options: []) { self.toast.alpha = 0 }
    }
}

extension ScreenViewController: UIGestureRecognizerDelegate {
    // dragging the button coexists with the tap that opens the menu
    func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
        true
    }
}
