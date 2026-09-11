import Java from 'frida-java-bridge';
setImmediate(function () {
  var out = {mods: [], httpcl: []};
  Process.enumerateModules().forEach(function (m) {
    if (/ssl|crypto|tnet|sgmain|conscrypt|quic|curl/i.test(m.name))
      out.mods.push(m.name + " @ " + m.base + " (" + m.size + ")");
  });
  Java.perform(function () {
    Java.enumerateLoadedClasses({
      onMatch: function (n) {
        if (/okhttp3|retrofit|com\.taobao\.tnet|com\.alibaba\.|com\.qwen|qwenlm/i.test(n) && out.httpcl.length < 25)
          out.httpcl.push(n);
      },
      onComplete: function () { send(out); }
    });
  });
});
