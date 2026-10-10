import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// Detail popup for the Throtl widget: shaping on/off switch + live rates +
// global caps + profile + top apps. Purely presentational — all data comes from
// the always-mounted BarWidget backend (injected as `backend`).
Panel {
  id: root
  moduleName: "flenser.throtl"
  ipcTarget: "flenser.throtl"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var backend: null
  readonly property var barIdentity: hostWidget || root

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color accent: Color.accent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color rule: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.18)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  function fmt(kbit) {
    if (kbit == null || isNaN(kbit) || kbit < 0) return "—"
    var bytes = kbit * 125
    if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB/s"
    if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB/s"
    if (bytes >= 1e3) return (bytes / 1e3).toFixed(0) + " KB/s"
    return Math.round(bytes) + " B/s"
  }

  function open() { root.controller.show() }
  function openFromHotkey() { root.controller.show() }
  function close() { root.controller.hide() }
  function toggle() { if (root.opened) root.close(); else root.open() }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(content.implicitHeight + Style.space(24))

    Flickable {
      id: contentScroll
      anchors.fill: parent
      anchors.margins: Style.space(16)
      clip: true
      contentWidth: width
      contentHeight: content.implicitHeight
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height

      Column {
        id: content
        width: parent.width
        spacing: Style.space(10)

        // ---- Header: title + shaping switch ----
        Row {
          width: parent.width
          spacing: Style.space(12)

          Column {
            width: parent.width - shapingSwitch.width - parent.spacing
            spacing: 2

            Text {
              text: "Throtl"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: 16
              font.weight: Font.DemiBold
              width: parent.width
            }

            Text {
              text: (root.backend && root.backend.iface)
                ? "Profile " + root.backend.profile + " · " + root.backend.iface
                : ""
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: 11
              elide: Text.ElideRight
              width: parent.width
            }
          }

          ToggleSwitch {
            id: shapingSwitch
            checked: root.backend ? root.backend.enabled : false
            foreground: root.foreground
            anchors.verticalCenter: parent.verticalCenter
            onToggled: { if (root.backend) root.backend.toggleShaping() }
          }
        }

        // ---- Rates ----
        Row {
          width: parent.width
          spacing: Style.space(16)

          Column {
            width: (parent.width - parent.spacing) / 2
            spacing: 1

            Text {
              text: "DOWNLOAD"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: 10
              font.weight: Font.DemiBold
              font.letterSpacing: 0.5
              width: parent.width
            }
            Text {
              text: "↓ " + (root.backend ? root.fmt(root.backend.downKbit) : "—")
              color: root.accent
              font.family: root.fontFamily
              font.pixelSize: 20
              font.weight: Font.Bold
              width: parent.width
              elide: Text.ElideRight
            }
          }

          Column {
            width: (parent.width - parent.spacing) / 2
            spacing: 1

            Text {
              text: "UPLOAD"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: 10
              font.weight: Font.DemiBold
              font.letterSpacing: 0.5
              width: parent.width
            }
            Text {
              text: "↑ " + (root.backend ? root.fmt(root.backend.upKbit) : "—")
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: 20
              font.weight: Font.Bold
              width: parent.width
              elide: Text.ElideRight
            }
          }
        }

        // ---- Divider ----
        Rectangle {
          width: parent.width
          height: 1
          color: root.rule
        }

        // ---- Info rows ----
        Column {
          width: parent.width
          spacing: Style.space(6)

          // Global caps
          Row {
            width: parent.width
            spacing: Style.space(8)
            Text {
              text: "Global caps"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: 12
              width: parent.width - capsValue.width - parent.spacing
              elide: Text.ElideRight
            }
            Text {
              id: capsValue
              text: {
                if (!root.backend) return ""
                var d = root.backend.globalDownLimit
                var u = root.backend.globalUpLimit
                if (d < 0 && u < 0) return "unlimited"
                var bits = []
                if (d >= 0) bits.push("↓ " + root.fmt(d))
                if (u >= 0) bits.push("↑ " + root.fmt(u))
                return bits.join("  ")
              }
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: 12
            }
          }

          // Rules
          Row {
            width: parent.width
            spacing: Style.space(8)
            Text {
              text: "Rules"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: 12
              width: parent.width - rulesValue.width - parent.spacing
              elide: Text.ElideRight
            }
            Text {
              id: rulesValue
              text: root.backend ? (root.backend.activeRules + " of " + root.backend.totalRules + " active") : ""
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: 12
            }
          }
        }

        // ---- Top apps ----
        Column {
          visible: root.backend && root.backend.apps && root.backend.apps.length > 0
          width: parent.width
          spacing: Style.space(6)

          Text {
            text: "TOP APPS"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: 10
            font.weight: Font.DemiBold
            font.letterSpacing: 0.5
            width: parent.width
          }

          Repeater {
            model: root.backend ? root.backend.apps : []

            Row {
              width: parent.width
              spacing: Style.space(8)

              Text {
                text: modelData.name
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: 12
                elide: Text.ElideRight
                width: parent.width - rateText.width - parent.spacing
              }

              Text {
                id: rateText
                text: "↓ " + root.fmt(modelData.down)
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: 12
              }
            }
          }
        }
      }
    }
  }
}
