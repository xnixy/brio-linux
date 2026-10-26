// KWin 6: report the active window's app id to briod.
function report(win) {
    var app = win ? (win.desktopFileName || win.resourceClass || "") : "";
    callDBus("io.github.BrioControl.Daemon", "/io/github/BrioControl/Daemon",
             "io.github.BrioControl1", "FocusChanged", String(app));
}
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
