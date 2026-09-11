var HOOKED = [];

function tryHook(modName, fnName, isWrite) {
  var m = Process.findModuleByName(modName);
  if (!m) return;
  var a = m.findExportByName(fnName);
  if (!a) return;
  Interceptor.attach(a, {
    onEnter: function (args) {
      this.buf = args[1];
      this.len = args[2].toInt32();
    },
    onLeave: function (ret) {
      var n = isWrite ? this.len : ret.toInt32();
      if (n > 0 && n < 262144) {
        try {
          var s = this.buf.readUtf8String(Math.min(n, 4000));
          if (s && s.length > 0) send({ t: isWrite ? 'W' : 'R', mod: modName, n: n, d: s });
        } catch (e) {}
      }
    }
  });
  HOOKED.push(modName + '!' + fnName);
}

['libssl.so', 'libcrypto_httpengine.so', 'libhttpengine.so', 'libcrypto.so'].forEach(function (m) {
  tryHook(m, 'SSL_write', true);
  tryHook(m, 'SSL_read', false);
});
send({ t: 'hooked', list: HOOKED });
