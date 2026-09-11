import Java from 'frida-java-bridge';
setImmediate(function () {
  Java.perform(function () {
    var out = {ok: true, sdk: Java.androidVersion};
    try {
      Java.enumerateLoadedClasses({
        onMatch: function (n) {
          if (/okhttp|tnet|qwenlm|alibaba/i.test(n) && out.sample === undefined) out.sample = n;
        },
        onComplete: function () {}
      });
    } catch (e) { out.ferr = String(e); }
    send(out);
  });
});
