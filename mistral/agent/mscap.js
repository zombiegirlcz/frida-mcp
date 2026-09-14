// mscap.js — odposlech Mistral appky (ai.mistral.chat, frida jmeno "Vibe").
//
// Appka je React Native/Expo a jede pres tRPC (/api/trpc, /api/code-trpc) na
// chat.mistral.ai. TLS jde pres Conscrypt, takze hookujeme vsechny varianty
// zapisu napric moduly (SSL_write byva i jako lokalni symbol).
//
// Posila do hostu: modul, symbol, metoda, URL, hlavicky a telo (plain text).

var MAXBODY = 262144;

function toStr(ptr, len) {
  try {
    var n = Math.min(len, MAXBODY);
    var b = new Uint8Array(ptr.readByteArray(n));
    var s = '';
    for (var i = 0; i < n; i++) {
      var c = b[i];
      s += (c >= 32 && c < 127) || c === 10 || c === 13 || c === 9
        ? String.fromCharCode(c) : '.';
    }
    return s;
  } catch (e) { return ''; }
}

var REQLINE = /^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+) HTTP/;

function report(mod, sym, text) {
  var idx = text.indexOf('\r\n\r\n');
  var head = idx >= 0 ? text.substring(0, idx) : text.substring(0, 32768);
  var body = idx >= 0 ? text.substring(idx + 4) : '';
  var lines = head.split('\r\n');
  var m = REQLINE.exec(lines[0]);
  if (!m) {
    // HTTP/2: zadny "POST /x HTTP/1.1" radek. Zajima nas jen provoz api.
    if (text.indexOf('/api/') === -1 && text.indexOf('mistral') === -1) return false;
    send({ mod: mod, sym: sym, method: 'H2?', url: 'http2-candidate',
           headers: {}, body: text.substring(0, 3000), h2: true });
    return true;
  }
  var headers = {};
  for (var i = 1; i < lines.length; i++) {
    var c = lines[i].indexOf(':');
    if (c > 0) headers[lines[i].substring(0, c).trim()] = lines[i].substring(c + 1).trim();
  }
  send({ mod: mod, sym: sym, method: m[1], url: m[2], headers: headers, body: body });
  return true;
}

var NAMES = ['SSL_write', 'SSL_write_ex', 'ssl_write', 'BIO_write',
             'NativeCrypto_SSL_write', 'NativeCrypto_BIO_write'];
var hooked = 0, fired = 0;

Process.enumerateModules().forEach(function (mod) {
  var syms = [];
  try { syms = mod.enumerateSymbols(); } catch (e) { return; }
  syms.forEach(function (s) {
    if (NAMES.indexOf(s.name) === -1) return;
    try {
      Interceptor.attach(s.address, {
        onEnter: function (a) {
          var n = (s.name === 'SSL_write_ex') ? a[3].toInt32() : a[2].toInt32();
          if (n <= 0 || n > 200000) return;
          var t = toStr(a[1], n);
          if (t.indexOf('/api/') === -1 && t.indexOf('mistral') === -1
              && t.indexOf(' HTTP/') === -1) return;
          if (report(mod.name, s.name, t)) fired++;
        }
      });
      hooked++;
    } catch (e) {}
  });
});
// exporty (kdyby symbol nebyl v enumerateSymbols)
Process.enumerateModules().forEach(function (mod) {
  NAMES.forEach(function (nm) {
    var p = null;
    try { p = mod.findExportByName(nm); } catch (e) {}
    if (!p) return;
    try {
      Interceptor.attach(p, {
        onEnter: function (a) {
          var n = a[2].toInt32();
          if (n <= 0 || n > 200000) return;
          var t = toStr(a[1], n);
          if (t.indexOf(' HTTP/') === -1) return;
          if (report(mod.name, nm + '(export)', t)) fired++;
        }
      });
      hooked++;
    } catch (e) {}
  });
});
send({ info: 'hooked=' + hooked });
