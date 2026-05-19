import SwiftUI

@main
struct ShadowrunEditorApp: App {
    @StateObject private var editor = EditorState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(editor)
                .frame(minWidth: 900, minHeight: 600)
                .task {
                    await editor.bootstrap()
                }
        }
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(after: .saveItem) {
                Button("Reload Save List") {
                    Task { await editor.rescanSaves() }
                }
                .keyboardShortcut("r")
            }
            CommandGroup(after: .undoRedo) {
                Button("Undo Last Edit") {
                    Task { await editor.undo() }
                }
                .disabled(editor.openSave?.pendingEdits.isEmpty ?? true)
                .keyboardShortcut("z")
            }
        }
    }
}
