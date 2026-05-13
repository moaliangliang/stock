// 东方财富 jywgmix.18.cn API 嗅探脚本
// 使用方法: 登录后 → 先粘贴运行此脚本 → 再点击页面菜单(持仓/资产)
// 用途: 拦截所有 fetch/XHR 请求，找出真实的 API 地址

(function(){
  if (window.__em_sniff_installed) {
    console.log("⚠️ 拦截器已安装，无需重复运行。直接点击页面菜单即可。");
    return;
  }
  window.__em_sniff_installed = true;
  window.__em_sniff_log = [];

  function log(item) {
    window.__em_sniff_log.push(item);
    console.log(item.prefix || "", item.url || item.msg || "");
  }

  // ── 劫持 fetch ──────────────────────────────────────────
  var _fetch = window.fetch;
  window.fetch = function(url, opts){
    var urlStr = typeof url === "string" ? url : (url.url || String(url));
    log({ prefix: "[FETCH] ▶", url: urlStr });

    return _fetch.apply(this, arguments).then(function(r){
      var clone = r.clone();
      clone.text().then(function(body){
        var short = body.length > 800 ? body.substring(0,800) + "..." : body;
        log({ prefix: "[FETCH] ◀ " + urlStr, url: short });
        // 尝试从响应中提取可能的目标字段
        var targets = ["userid","userId","ctToken","utToken","fundaccount","fundAccount",
                       "secuid","secUid","cashaccount","cashAccount","uid","Uid"];
        targets.forEach(function(k){
          if (body.indexOf('"'+k+'"') >= 0 || body.indexOf("'"+k+"'") >= 0) {
            var re = new RegExp('["\\']?' + k + '["\\']?\\s*[:=]\\s*["\\']?([^"\\',;\\s}]+)', 'i');
            var m = body.match(re);
            if (m) {
              console.log("  ⭐ 发现字段: " + k + " = " + m[1]);
            }
          }
        });
      }).catch(function(){});
      return r;
    });
  };

  // ── 劫持 XHR ────────────────────────────────────────────
  var _open = XMLHttpRequest.prototype.open;
  var _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url){
    this.__em_url = url;
    this.__em_method = method;
    return _open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body){
    var self = this;
    log({ prefix: "[XHR] ▶", url: self.__em_method + " " + self.__em_url });
    self.addEventListener("load", function(){
      var short = self.responseText.length > 800 ? self.responseText.substring(0,800) + "..." : self.responseText;
      log({ prefix: "[XHR] ◀ " + self.__em_url, url: short });
    });
    return _send.call(this, body);
  };

  // ── 提示 ──────────────────────────────────────────────
  console.log("═══════════════════════════════════════════");
  console.log("  ✅ API 嗅探器已就绪");
  console.log("  现在点击页面左侧菜单：持仓 / 资产 / 资金股份");
  console.log("  所有 API 请求会打印在下方");
  console.log("═══════════════════════════════════════════");
  console.log("");
  console.log("📋 全部捕获完成后，运行 copy(JSON.stringify(window.__em_sniff_log)) 可一键复制结果");
})();
