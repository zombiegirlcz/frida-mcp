import Java from 'frida-java-bridge';

// Zachyti plaintext TLS provoz appky (chat.qwen.ai) pres bundled libssl.so.
// Loguje hlavicky + telo (zkracene) do send().

var MAX = 4000;

function toStr(ptr, len) {
  try {
    var n = Math.min(len, MAX);
    var bytes = new Uint8Array(ptr.readByteArray(n));
    var s = '';
    for (var i = 0; i < n; i++) {
      var c = bytes[i];
      s += (c >= 32 && c < 127) || c === 10 || c === 13 || c === 9
        ? String.fromCharCode(c) : '.';
    }
    return s;
  } catch (e) { return '<err ' + e + '>'; }
}

function hook(lib, fn, isWrite) {
  var m;
  try { m = Process.getModuleByName(lib); } catch (e) { return false; }
  var exp;
  try { exp = m.getExportByName(fn); } catch (e) { return false; }
  Interceptor.attach(exp, {
    onEnter: function (args) {
      if (isWrite) {
        try { send({ dir: 'OUT', fn: fn, text: toStr(args[1], args[2].toInt32()) }); } catch (e) {}
      } else {
        this.buf = args[1];
        this.len = args[2].toInt32();
      }
    },
    onLeave: function (ret) {
      if (!isWrite) {
        try {
          var n = ret.toInt32();
          if (n > 0) send({ dir: 'IN', fn: fn, text: toStr(this.buf, n) });
        } catch (e) {}
      }
    }
  });
  send({ hooked: lib + '!' + fn });
  return true;
}

setImmediate(function () {
  var r = [];
  r.push(hook('libssl.so', 'SSL_write', true));
  r.push(hook('libssl.so', 'SSL_read', false));
  r.push(hook('libssl.so', 'SSL_write_ex', true));
  r.push(hook('libssl.so', 'SSL_read_ex', false));
  send({ result: r });
});
