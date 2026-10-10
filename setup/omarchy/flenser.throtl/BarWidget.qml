import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Throtl bar pill + backend: live download/upload, click opens a detail popup
// with a shaping toggle. Polls `throtl-cli list-processes --json` for rates and
// `throtl-cli get_config` for caps/profile; toggles shaping with `throtl-cli
// toggle` (no arg = flip the current state).
BarWidget {
  id: root
  moduleName: "flenser.throtl"

  readonly property var cfg: settings || {}

  property bool enabled: true
  property real downKbit: 0
  property real upKbit: 0
  property string iface: ""
  property var apps: []
  property real globalDownLimit: -1   // -1 = unlimited
  property real globalUpLimit: -1
  property string profile: "Standard"
  property int activeRules: 0
  property int totalRules: 0
  property bool loaded: false

  // kbit/s -> human bytes/s ("1.3 MB/s", "512 KB/s", "2.1 GB/s").
  function fmt(kbit) {
    if (kbit == null || isNaN(kbit) || kbit < 0) return "—"
    var bytes = kbit * 125   // 1 kbit/s = 125 bytes/s
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB/s"
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB/s"
    if (bytes >= 1e3) return (bytes / 1e3).toFixed(0) + " KB/s"
    return Math.round(bytes) + " B/s"
  }

  readonly property string label: {
    if (!loaded) return "↓ …"
    if (!enabled) return "OFF"
    return "↓" + fmt(downKbit) + " ↑" + fmt(upKbit)
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!poll.running) {
      poll.command = ["throtl-cli", "list-processes", "--json"]
      poll.running = true
    }
    if (!cfgPoll.running) {
      cfgPoll.command = ["throtl-cli", "get_config"]
      cfgPoll.running = true
    }
  }

  function apply(raw) {
    if (!raw || raw === "") return
    try {
      var data = JSON.parse(raw)
      enabled = data.enabled === true
      var g = data.global || {}
      downKbit = Number(g.download) || 0
      upKbit = Number(g.upload) || 0
      iface = String(data.interface || "")
      var list = (data.apps || []).filter(function (a) { return !a.unattributed })
      list.sort(function (a, b) {
        return (Number(b.download) + Number(b.upload)) - (Number(a.download) + Number(a.upload))
      })
      apps = list.slice(0, 6).map(function (a) {
        return { name: String(a.name || ""), down: Number(a.download) || 0, up: Number(a.upload) || 0 }
      })
      loaded = true
    } catch (e) {
      // keep last known values on a transient parse error
    }
  }

  function applyCfg(raw) {
    if (!raw || raw === "") return
    try {
      var c = JSON.parse(raw)
      var g = c.global || {}
      globalDownLimit = (g.download_limit != null && g.download_limit !== "") ? Number(g.download_limit) : -1
      globalUpLimit = (g.upload_limit != null && g.upload_limit !== "") ? Number(g.upload_limit) : -1
      profile = String(c.active_profile || "Standard")
      var rules = c.processes || []
      totalRules = rules.length
      activeRules = rules.filter(function (r) {
        return r.download_limit != null || r.upload_limit != null
      }).length
    } catch (e) {
      // ignore
    }
  }

  // Flip global shaping, then refresh so the pill + panel reflect the new state.
  function toggleShaping() {
    if (toggleProc.running) return
    enabled = !enabled   // optimistic flip; the refresh below confirms it
    toggleProc.command = ["throtl-cli", "toggle"]
    toggleProc.running = true
    postToggle.restart()
  }

  Process {
    id: poll
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.apply(String(text || "").trim())
    }
  }

  Process {
    id: cfgPoll
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyCfg(String(text || "").trim())
    }
  }

  Process {
    id: toggleProc
    stdout: StdioCollector { waitForEnd: true }
  }

  Timer {
    id: postToggle
    interval: 800
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: refreshTimer
    interval: (root.cfg && root.cfg.refreshIntervalSec ? root.cfg.refreshIntervalSec : 2) * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  // ---- Detail popup ----
  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  property bool _openedFallback: false
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : _openedFallback

  function open() {
    if (panelLoader.item) panelLoader.item.open()
    else _openedFallback = true
  }
  function close() {
    if (panelLoader.item) panelLoader.item.close()
    else _openedFallback = false
  }
  function openFromHotkey() { root.open() }
  function toggle() {
    if (opened) root.close()
    else root.open()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("backend" in target) target.backend = root
  }

  onBarChanged: injectPanel()

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.label
    labelVisible: text !== ""
    hasVisualContent: text !== ""
    horizontalMargin: 8.75
    verticalPadding: 8.75
    onPressed: function (b) {
      if (!root.bar) return
      if (b === Qt.RightButton || b === Qt.MiddleButton) root.refresh()
      else root.toggle()
    }
  }
}
