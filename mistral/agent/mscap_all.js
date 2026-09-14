// mscap_all.js — dump VSECH SSL_write dat (nic nefiltruje).
//
// Proc: chat.mistral.ai jede pres HTTP/2. Hlavicky jsou HPACK/Huffman
// (cesta neni v plaintextu), ale DATA ramce s TELEM komprimovane nejsou —
// tam je cely JSON pozadavku (prompt, model, mod, tools, mcp).
var MAX = 65536;
function toStr(ptr, len) {
  try {
    var n = Math.min(len, MAX);
    var b = new Uint8Array(ptr.readByteArray(n));
    var s = '';
    for (var i = 0; i < n; i++) {
      var c = b[i];
      s += (c >= 32 && c < 127) || c === 10 || c === 13 || c === 9
        ? String.fromCharCode(c) : '\\x' + (c < 16 ? '0' : '') + c.toString(16);
    }
    return s;
  } catch (e) { return ''; }
}
var hooked = 0, n = 0;
var NAMES = ['SSL_write', 'SSL_write_ex', 'ssl_write', 'BIO_write'];

function hookAddr(mod, name, addr, isEx) {
  try {
    Interceptor.attach(addr, {
      onEnter: function (a) {
        var len = (name === 'SSL_write_ex') ? a[3].toInt32() : a[2].toInt32();
        if (len <= 8 || len > 500000) return;
        var t = toStr(a[1], len);
        n++;
        send({ n: n, mod: mod + (isEx ? '(export)' : ''), len: len, data: t });
      }
    });
    hooked++;
  } catch (e) {}
}

// 1) pres vsechny symboly (vcetne lokalnich)
Process.enumerateModules().forEach(function (mod) {
  var syms = [];
  try { syms = mod.enumerateSymbols(); } catch (e) { return; }
  syms.forEach(function (s) {
    if (NAMES.indexOf(s.name) === -1) return;
    hookAddr(mod.name, s.name, s.address, false);
  });
});
// 2) pres EXPORTY (ty v enumerateSymbols casto nejsou!)
Process.enumerateModules().forEach(function (mod) {
  NAMES.forEach(function (nm) {
    var a = null;
    try { a = mod.findExportByName(nm); } catch (e) {}
    if (a) hookAddr(mod.name, nm, a, true);
  });
});
send({ info: 'hooked=' + hooked });
