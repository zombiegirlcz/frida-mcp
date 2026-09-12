// dsdiag.js — zjisti, KTEROU vrstvou DeepSeek appka posila data.
//
// DeepSeek appka nepouziva systemovy libssl.so. V procesu je nekolik vlastnich
// TLS/http knihoven (libhttpengine.so, libcrypto_httpengine.so, libjavacrypto.so)
// a casto jde o LOKALNI (mangled) symboly, ktere `enumerateExports` nevidi.
//
// Tento skript hookne vsechny kandidaty a posila pocitadla zásahu, aby bylo
// jasne, ktera funkce se realne vola.

var counters = {};
var hits = [];

function bump(key, extra) {
  counters[key] = (counters[key] || 0) + 1;
  if (hits.length < 40) {
    var rec = { type: 'hit', key: key };
    if (extra) rec.info = extra;
    hits.push(rec);
    send(rec);
  }
}

function safeStr(ptr, len, max) {
  try {
    var n = Math.min(len, max || 64);
    var bytes = new Uint8Array(ptr.readByteArray(n));
    var s = '';
    for (var i = 0; i < n; i++) {
      var c = bytes[i];
      if (c >= 32 && c < 127) s += String.fromCharCode(c);
      else if (c === 10 || c === 13) s += ' ';
      else if (c === 0) break;
      else s += '.';
    }
    return s;
  } catch (e) { return ''; }
}

function hookAll() {
  var result = { sslLike: [], libc: [], other: [] };
  var seen = {};

  Process.enumerateModules().forEach(function (m) {
    // 1) cokoli, co ma v nazve symbolu SSL_write (vcetne mangled JNI jmen)
    try {
      m.enumerateSymbols().forEach(function (s) {
        var nm = s.name;
        if (nm.indexOf('SSL_write') < 0 && nm.indexOf('ssl_write') < 0) return;
        var k = s.address.toString();
        if (seen[k]) return;
        seen[k] = true;
        try {
          Interceptor.attach(s.address, {
            onEnter: function (args) {
              bump(m.name + '!' + nm, safeStr(args[1], 48));
            },
          });
          result.sslLike.push(m.name + '!' + nm);
        } catch (e) {}
      });
    } catch (e) {}

    // 2) obecne zapisy v libc (jen pocitadlo, at vime, ze vubec neco odeslo)
    if (m.name === 'libc.so') {
      ['write', 'send', 'sendto', 'writev', 'sendmsg'].forEach(function (fn) {
        try {
          var a = m.findExportByName(fn);
          if (!a) return;
          var k = a.toString();
          if (seen[k]) return;
          seen[k] = true;
          Interceptor.attach(a, {
            onEnter: function (args) {
              var fd = args[0].toInt32();
              var len = args[2].toInt32();
              if (len > 20) bump('libc!' + fn, 'fd=' + fd + ' len=' + len);
            },
          });
          result.libc.push('libc!' + fn);
        } catch (e) {}
      });
    }

    // 3) BIO_write — BoringSSL pres nej casto zapisuje
    try {
      m.enumerateSymbols().forEach(function (s) {
        if (s.name !== 'BIO_write') return;
        var k = s.address.toString();
        if (seen[k]) return;
        seen[k] = true;
        try {
          Interceptor.attach(s.address, {
            onEnter: function (args) { bump(m.name + '!BIO_write'); },
          });
          result.other.push(m.name + '!BIO_write');
        } catch (e) {}
      });
    } catch (e) {}
  });

  send({ type: 'hooked', result: result });
}

setImmediate(hookAll);
setInterval(function () {
  send({ type: 'counters', counters: counters });
}, 5000);
