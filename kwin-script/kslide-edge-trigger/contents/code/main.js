// PEEK Edge Trigger — KWin Script (Plasma 6)
//
// Registra o canto superior direito da tela como trigger.
// Quando o mouse encosta no canto, chama Toggle no D-Bus do PEEK.
//
// ElectricBorder enum (KWin):
//   Top=0  TopRight=1  Right=2  BottomRight=3
//   Bottom=4  BottomLeft=5  Left=6  TopLeft=7

(function () {
    "use strict";

    var EDGE_TOP_RIGHT = 1; // ElectricBorder::ElectricTopRight

    var DBUS_SERVICE   = "org.peek.App";
    var DBUS_PATH      = "/App";
    var DBUS_INTERFACE = "org.peek.App";
    var DBUS_METHOD    = "Toggle";

    registerScreenEdge(EDGE_TOP_RIGHT, function () {
        callDBus(DBUS_SERVICE, DBUS_PATH, DBUS_INTERFACE, DBUS_METHOD);
    });
})();
