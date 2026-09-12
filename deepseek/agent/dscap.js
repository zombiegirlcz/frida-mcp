// dscap.js — zachytava odchozi HTTP requesty DeepSeek appky.
//
// ⚠️ DeepSeek appka NEPOUZIVA systemovy libssl.so. Ma vlastni BoringSSL
// zkopirovany v `libhttpengine.so`, kde je `SSL_write` jen LOKALNI symbol
// (neni v exportech, najde se jen pres enumerateSymbols). Proto hookujeme
// vsechny vyskytu `SSL_write` napric moduly.
//
// Posila do hostu: metoda, cesta, hlavicky a PLAIN TEXT BODY (u chat/completion
// je v tele cely prompt a vsechny prepinace jako thinking_enabled).
//
// Bez bundlingu (nepouziva Java bridge).

var MAXHEAD = 32768;
var MAXBODY = 262144;

function toStr(ptr, len, max) {
  try {
    var n = Math.min(len, max);
    var bytes = new Uint8Array(ptr.readByteArray(n));
    var s = '';
    for (var i = 0; i < n; i++) {
      var c = bytes[i];
      s += (c >= 32 && c < 127) || c === 10 || c === 13 || c === 9
        ? String.fromCharCode(c) : '.';
    }
    return s;
  } catch (e) { return ''; }
}

var REQLINE = /^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD) (\S+) HTTP/;

function parseReq(text) {
  var idx = text.indexOf('\r\n\r\n');
  var head = idx >= 0 ? text.substring(0, idx) : text.substring(0, MAXHEAD);
  var body = idx >= 0 ? text.substring(idx + 4) : '';
  var lines = head.split('\r\n');
  var m = REQLINE.exec(lines[0]);
  if (!m) return null;
  var headers = {};
  for (var i = 1; i < lines.length; i++) {
    var p = lines[i].indexOf(': ');
    if (p > 0) headers[lines[i].substring(0, p).toLowerCase()] = lines[i].substring(p + 2);
  }
  var out = { type: 'req', method: m[1], path: m[2], headers: headers };
  if (body) out.body = body.substring(0, MAXBODY);
  return out;
}

// najdi vsechny adresy funkci, ktere vypadaji jako SSL_write
function findWrites() {
  var found = [];
  var seen = {};
  Process.enumerateModules().forEach(function (m) {
    var add = function (addr, label) {
      var k = addr.toString();
      if (seen[k]) return;
      seen[k] = true;
      found.push({ mod: m.name, label: label, addr: addr });
    };
    try {
      m.enumerateSymbols().forEach(function (s) {
        if (s.name === 'SSL_write' || s.name === 'SSL_write_ex'
            || s.name === 'SSL_write_early_data') add(s.address, s.name);
      });
    } catch (e) {}
    try {
      var e2 = m.findExportByName('SSL_write');
      if (e2) add(e2, 'SSL_write(export)');
    } catch (e) {}
    try {
      var e3 = m.findExportByName('SSL_write_ex');
      if (e3) add(e3, 'SSL_write_ex(export)');
    } catch (e) {}
  });
  return found;
}

function attach() {
  var targets = findWrites();
  if (!targets.length) {
    send({ type: 'error', error: 'SSL_write nikde nenalezen' });
    return;
  }
  targets.forEach(function (t) {
    try {
      Interceptor.attach(t.addr, {
        onEnter: function (args) {
          try {
            // SSL_write(ssl, buf, num) — u _ex je to (ssl, buf, num, *written)
            var r = parseReq(toStr(args[1], args[2].toInt32(), MAXHEAD));
            if (r) {
              r.via = t.mod + ':' + t.label;
              send(r);
            }
          } catch (e) {}
        },
      });
      send({ type: 'hooked', mod: t.mod, label: t.label, addr: t.addr.toString() });
    } catch (e) {
      send({ type: 'error', error: 'hook ' + t.mod + ' selhal: ' + e });
    }
  });
  send({ type: 'ready', count: targets.length });
}

setImmediate(attach);
