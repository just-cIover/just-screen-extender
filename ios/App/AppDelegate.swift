import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        UISceneConfiguration(name: "Default", sessionRole: connectingSceneSession.role)
    }
}

/// Una sola finestra: lo schermo del PC. Si collega quando l'app è in primo piano e si scollega
/// quando va in background (il PC spegne il suo schermo virtuale dopo 15 secondi).
final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private let screen = ScreenViewController()

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = screen
        window.makeKeyAndVisible()
        self.window = window
        // aperta con un file da un'altra app (Condividi → Schermo Esteso)
        screen.receiveShared(connectionOptions.urlContexts.map(\.url))
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        screen.receiveShared(URLContexts.map(\.url))
    }

    func sceneDidBecomeActive(_ scene: UIScene) {
        screen.resume()
        screen.becameActive()
    }
    // Centro di controllo, app switcher, notifiche: iPadOS ferma l'app per un attimo
    func sceneWillResignActive(_ scene: UIScene) { screen.resignedActive() }
    func sceneDidEnterBackground(_ scene: UIScene) { screen.pause() }
}
