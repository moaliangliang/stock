// 东方财富 jywgmix.18.cn 凭证抓取脚本
// 使用方法: 浏览器登录 https://jywgmix.18.cn/ → F12 → Console → 粘贴运行
// 用途: 提取 EM_ACCOUNT_USERID / CT_TOKEN / UT_TOKEN / FUND_ACCOUNT / SECUID

(function(){
  // ── 目标字段及其可能的 cookie 名称 ───────────────────────
  var TARGETS = [
    { names: ["userid","userId","uid","Uid"],                  key: "EM_ACCOUNT_USERID" },
    { names: ["ctToken","ct_token","cttoken","CTToken"],       key: "EM_ACCOUNT_CT_TOKEN" },
    { names: ["utToken","ut_token","ut","uttoken","UTToken"],  key: "EM_ACCOUNT_UT_TOKEN" },
    { names: ["fundaccount","fundAccount","cashaccount","cashAccount","fund_account"], key: "EM_ACCOUNT_FUND_ACCOUNT" },
    { names: ["secuid","secUid","sec_uid","secuid","Secuid"],  key: "EM_ACCOUNT_SECUID" },
  ];

  var found = {};

  // ── 1. 扫描 Cookies ──────────────────────────────────────
  var cookies = {};
  document.cookie.split(";").forEach(function(c){
    var kv = c.trim().split("=");
    if (kv.length >= 2) cookies[kv[0].trim()] = kv.slice(1).join("=");
  });

  function matchIn(source, srcName) {
    TARGETS.forEach(function(t){
      if (found[t.key]) return;
      t.names.forEach(function(name){
        if (source[name] !== undefined && source[name] !== "" && source[name] !== null) {
          found[t.key] = { value: source[name], source: srcName + " → " + name };
        }
      });
      // 大小写不敏感
      if (!found[t.key]) {
        Object.keys(source).forEach(function(k){
          if (k.toLowerCase() === name.toLowerCase()) {
            found[t.key] = { value: source[k], source: srcName + " → " + k + " (case-insensitive)" };
          }
        });
      }
    });
  }

  matchIn(cookies, "Cookie");

  // ── 2. 扫描 localStorage / sessionStorage ────────────────
  var ls = {}, ss = {};
  try { for (var i=0; i<localStorage.length; i++){ var k=localStorage.key(i); ls[k]=localStorage.getItem(k); } } catch(e){}
  try { for (var i=0; i<sessionStorage.length; i++){ var k=sessionStorage.key(i); ss[k]=sessionStorage.getItem(k); } } catch(e){}
  matchIn(ls, "localStorage");
  matchIn(ss, "sessionStorage");

  // 尝试解析 JSON 值
  function scanJSON(str, label) {
    if (!str || str.length < 2) return;
    try { var obj = JSON.parse(str); if (typeof obj === "object" && obj) matchIn(obj, label); } catch(e){}
  }
  Object.keys(ls).forEach(function(k){ scanJSON(ls[k], "localStorage[\""+k+"\"]"); });
  Object.keys(ss).forEach(function(k){ scanJSON(ss[k], "sessionStorage[\""+k+"\"]"); });

  // ── 3. 打印本地扫描结果 ──────────────────────────────────
  console.log("═══════════════════════════════════════════");
  console.log("  本地 Cookie/Storage 扫描");
  console.log("═══════════════════════════════════════════");
  var missing = [];
  TARGETS.forEach(function(t){
    if (found[t.key]) {
      var v = found[t.key].value;
      var display = (typeof v === "string" && v.length > 80) ? v.substring(0,80)+"..." : v;
      console.log("✅ " + t.key + " = " + display + "  [" + found[t.key].source + "]");
    } else {
      console.log("❌ " + t.key + " — 未找到");
      missing.push(t.key);
    }
  });
  console.log("");
  console.log("本地已找到: " + (TARGETS.length - missing.length) + "/" + TARGETS.length);
  if (missing.length) console.log("缺失: " + missing.join(", "));

  // ── 4. 调用真实持仓 API 并从响应中提取漏掉的字段 ────────
  console.log("");
  console.log("═══════════════════════════════════════════");
  console.log("  调用 /Com/queryAssetAndPositionV1 ...");
  console.log("═══════════════════════════════════════════");

  fetch("/Com/queryAssetAndPositionV1", {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "Accept": "application/json, text/plain, */*"
    },
    body: JSON.stringify({moneyType: "RMB"})
  })
  .then(function(r){ return r.text(); })
  .then(function(text){
    console.log("API 响应 (前2000字符):");
    console.log(text.substring(0, 2000));
    console.log("");

    // 从响应中搜索目标字段
    try {
      var obj = JSON.parse(text);
      if (obj.Status === "0" && obj.Data && obj.Data[0]) {
        var data = obj.Data[0];
        console.log("✅ API 调用成功！");
        console.log("  总资产: " + data.Zzc);
        console.log("  可用资金: " + data.Kyzj);
        console.log("  持仓数: " + (data.positions ? data.positions.length : 0));
        console.log("  债券/逆回购数: " + (data.bonds ? data.bonds.length : 0));
      }
    } catch(e){}

    // 正则搜素可能的目标字段
    var patterns = [
      { regex: /"ctToken"\s*:\s*"([^"]+)"/,  key: "EM_ACCOUNT_CT_TOKEN" },
      { regex: /"utToken"\s*:\s*"([^"]+)"/,  key: "EM_ACCOUNT_UT_TOKEN" },
      { regex: /"secuid"\s*:\s*"([^"]+)"/,   key: "EM_ACCOUNT_SECUID" },
      { regex: /"fundaccount"\s*:\s*"([^"]+)"/, key: "EM_ACCOUNT_FUND_ACCOUNT" },
      { regex: /"userId"\s*:\s*(\d+)/,       key: "EM_ACCOUNT_USERID" },
      { regex: /"cashaccount"\s*:\s*"([^"]+)"/, key: "EM_ACCOUNT_FUND_ACCOUNT" },
    ];
    patterns.forEach(function(p){
      if (!found[p.key]) {
        var m = text.match(p.regex);
        if (m) found[p.key] = { value: m[1], source: "API response regex" };
      }
    });

    // ── 5. 最终结果 ────────────────────────────────────────
    console.log("");
    console.log("═══════════════════════════════════════════");
    console.log("  最终结果");
    console.log("═══════════════════════════════════════════");
    TARGETS.forEach(function(t){
      if (found[t.key]) {
        console.log("✅ " + t.key + " = " + found[t.key].value);
      } else {
        console.log("❌ " + t.key + " — 未找到");
      }
    });

    // 一键复制 .env 配置
    console.log("");
    console.log("────────── 复制到 .env ──────────");
    var envLines = [];
    TARGETS.forEach(function(t){
      var v = found[t.key] ? found[t.key].value : "";
      envLines.push(t.key + "=" + v);
    });
    console.log(envLines.join("\n"));
    console.log("EM_ACCOUNT_BASE_URL=https://jywgmix.18.cn");
    console.log("EM_ACCOUNT_SYNC_ENABLED=true");
    console.log("──────────────────────────────────");
  })
  .catch(function(e){
    console.error("API 调用失败: " + e);
    console.log("");
    console.log("💡 这可能是因为 Cookie 认证没通过。请确认你已登录 jywgmix.18.cn");
  });

  // ── 6. 输出完整 Cookie 清单供人工复查 ──────────────────
  console.log("");
  console.log("═══════════════════════════════════════════");
  console.log("  完整 Cookies (" + Object.keys(cookies).length + " 个)");
  console.log("═══════════════════════════════════════════");
  Object.keys(cookies).sort().forEach(function(k){
    var v = cookies[k];
    console.log("  " + k + " = " + (v.length > 100 ? v.substring(0,100)+"..." : v));
  });
})();
