import Java from 'frida-java-bridge';
function log(o){ send(o); }
Java.perform(function () {
  var PC = Java.use('com.deepseek.crypto.PowCalculator');
  PC.nativeCalculateDeepSeekHashV1Pow.implementation = function (a, b, c) {
    var r = this.nativeCalculateDeepSeekHashV1Pow(a, b, c);
    log({ t: 'pow', arg1: String(a), arg2: String(b), arg3: String(c), result: String(r) });
    return r;
  };
  log({ t: 'log', m: 'PowCalculator hooked' });
});
