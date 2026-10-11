"""Build the single-file Permian Title Playbook page from the fragments in this folder.

Run from playbook/src:  python build.py   ->  ../permian-title-playbook.html
The schema shown on the page (schema.json) is the playbook's illustrative version;
the kit's enforced schema is ptk/schema/instrument.schema.json.
"""
import html
from pathlib import Path

HERE = Path(__file__).resolve().parent
read = lambda p: (HERE / p).read_text(encoding="utf-8")
schema = read('schema.json')

toc = [("thesis","Start here"),("scope","Five title products"),("sop","The run, step by step"),("counties","County finder"),
       ("texas","Texas"),("newmexico","New Mexico"),("redflags","Red flags"),("math","Decimal calculator"),
       ("automation","Automation"),("tools","Tool stack"),("qc","QC and KPIs"),("team","People and costs"),
       ("plan","First 90 days"),("sources","Sources")]
toc_html = "\n".join(f'<li><a href="#{i}">{t}</a></li>' for i, t in toc)

auto = read('s_auto.html').replace('__SCHEMA__', html.escape(schema))
# order: core, counties, texas, nm, redflags (inside s_qc first section) ... split s_qc so red flags precede calc and qc comes later
qc = read('s_qc.html')
redflags, qcsec = qc.split('<section id="qc">')
qcsec = '<section id="qc">' + qcsec
main = (read('s_core.html') + read('s_counties.html') + read('s_tx.html') + read('s_nm.html') + redflags +
        read('s_calc.html') + auto + read('s_tools.html') + qcsec + read('s_plan.html'))

page = f'''<title>Permian Title Playbook</title>
<meta name="description" content="How to run mineral and lease title across the Texas and New Mexico Permian Basin: fast, defensible, and as automated as the records allow.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&display=swap">
<style>
{read('style.css')}
</style>
<div class="wrap">
  <header class="mast">
    <div>
      <div class="eyebrow">Land department operating manual · Texas &amp; New Mexico Permian</div>
      <h1>Permian Title Playbook</h1>
      <p class="lede">How a minerals and land company runs title across the Midland and Delaware Basins: what to search, where the records are, which tools to buy, and how to automate everything except the judgment calls.</p>
    </div>
    <div class="stamp" aria-label="Prepared October 1, 2026">Filed for record<b>Oct 1, 2026</b>TX + NM Permian · 24 counties</div>
  </header>
  <div class="layout">
    <nav class="toc" aria-label="Contents">
      <details open id="toc-details"><summary>Contents</summary>
        <ol>{toc_html}</ol>
      </details>
    </nav>
    <main>
{main}
      <footer>Landman work product for internal planning. It is not a title opinion or legal advice. Ownership conclusions that need legal judgment belong to your examining attorney.</footer>
    </main>
  </div>
</div>
<script>
{read('calc.js')}
{read('counties_tx.js')}
{read('counties_nm.js')}
{read('app.js')}
(function () {{
  var d = document.getElementById("toc-details");
  if (d && window.matchMedia && window.matchMedia("(max-width: 920px)").matches) d.open = false;
}})();
</script>
'''
(HERE.parent / 'permian-title-playbook.html').write_text(page, encoding='utf-8')
print(len(page), "bytes")
