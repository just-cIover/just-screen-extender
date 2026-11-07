import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     configurationForConnecting connectingSceneSession: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        UISceneConfiguration(name: "Default", sessionRole: connectingSceneSession.role)
    }
}

/// A single window: the PC screen. It connects when the app is in the foreground and disconnects
/// when it goes to the background (the PC turns its virtual screen off after 15 seconds).
final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private let screen = ScreenViewController()

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = screen
        window.makeKeyAndVisible()
        self.window = window
        // opened with a file from another app (Share → Screen Extender)
        screen.receiveShared(connectionOptions.urlContexts.map(\.url))
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        screen.receiveShared(URLContexts.map(\.url))
    }

    func sceneDidBecomeActive(_ scene: UIScene) {
        screen.resume()
        screen.becameActive()
    }
    // Control Center, app switcher, notifications: iPadOS pauses the app for a moment
    func sceneWillResignActive(_ scene: UIScene) { screen.resignedActive() }
    func sceneDidEnterBackground(_ scene: UIScene) { screen.pause() }
}
