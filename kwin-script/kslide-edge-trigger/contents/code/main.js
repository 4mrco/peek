// PEEK Edge Trigger — KWin Script (Plasma 6)
//
// Registra o canto superior direito (1) ou esquerdo (7) conforme configurado.
// Quando o mouse encosta no canto, chama Toggle no D-Bus do PEEK.
//
// ElectricBorder enum (KWin):
//   Top=0  TopRight=1  Right=2  BottomRight=3
//   Bottom=4  BottomLeft=5  Left=6  TopLeft=7

(function () {
    "use strict";

    var DBUS_SERVICE   = "org.peek.App";
    var DBUS_PATH      = "/App";
    var DBUS_INTERFACE = "org.peek.App";
    var DBUS_METHOD    = "Toggle";

    var activeEdge = -1;

    function resolveEdge() {
        var raw = parseInt(readConfig("TriggerEdge", 1), 10);
        return (raw === 7) ? 7 : 1;
    }

    function onEdgeTriggered() {
        callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, DBUS_METHOD);
    }

    function applyEdge() {
        var nextEdge = resolveEdge();
        if (nextEdge === activeEdge) {
            return;
        }
        if (activeEdge !== -1 && typeof unregisterScreenEdge === "function") {
            unregisterScreenEdge(activeEdge);
        }
        activeEdge = nextEdge;
        registerScreenEdge(activeEdge, onEdgeTriggered);
    }

    applyEdge();
    if (typeof options !== "undefined" && options.configChanged) {
        options.configChanged.connect(applyEdge);
    }
})();
