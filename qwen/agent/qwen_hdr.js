// qwen_hdr.js — zachytava odchozi HTTP requesty Qwen appky (libssl.so) a posila
// jejich hlavicky do hostu. Bez bundlingu (nepouziva Java bridge).
//
// Pouziti: bridge/qwen_hdrd.py (attacha na proces "Qwen Studio", load tohoto skriptu)

var MAXHEAD = 16384;
var MAXBODY = 4000;

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

function attach() {
  var mod;
  try { mod = Process.getModuleByName('libssl.so'); }
  catch (e) { send({ type: 'error', error: 'libssl.so nenalezen: ' + e }); return; }
  var exp;
  try { exp = mod.getExportByName('SSL_write'); }
  catch (e) { send({ type: 'error', error: 'SSL_write nenalezen' }); return; }

  Interceptor.attach(exp, {
    onEnter: function (args) {
      try {
        var r = parseReq(toStr(args[1], args[2].toInt32(), MAXHEAD));
        if (r) send(r);
      } catch (e) {}
    }
  });
  send({ type: 'ready', base: mod.base.toString() });
}

setImmediate(attach);
