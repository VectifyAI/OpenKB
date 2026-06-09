import os, re, glob, pathlib, statistics, sys
sys.path.insert(0, ".")
import yaml, litellm
from openkb.retrieval import select_relevant_briefs, select_relevant_briefs_embed
litellm.suppress_debug_info = True

KB = pathlib.Path(os.environ.get("OPENKB_KB", "kb"))
# key
for ln in (KB/".env").read_text().splitlines():
    if ln.startswith("LLM_API_KEY="): os.environ["OPENAI_API_KEY"]=ln.split("=",1)[1].strip()

def body_and_fm(text):
    if text.startswith("---"):
        end=text.find("---",3)
        if end!=-1:
            try: fm=yaml.safe_load(text[3:end]) or {}
            except Exception: fm={}
            return text[end+3:], fm
    return text, {}

# concept briefs -> formatted lines "- slug: brief" (slug recoverable by parse)
concept_lines=[]
for p in sorted((KB/"wiki/concepts").glob("*.md")):
    body,fm=body_and_fm(p.read_text())
    brief = fm.get("brief") if isinstance(fm.get("brief"),str) else ""
    if not brief: brief=body.strip().replace("\n"," ")[:150]
    concept_lines.append(f"- {p.stem}: {brief}")
block="\n".join(concept_lines)
valid_slugs={ln[2:].split(":",1)[0] for ln in concept_lines}
print(f"concepts: {len(concept_lines)}")

LINK=re.compile(r"\[\[concepts/([^\]\|]+)(?:\|[^\]]*)?\]\]")
samples=[]
for p in sorted((KB/"wiki/summaries").glob("*.md")):
    body,_=body_and_fm(p.read_text())
    gt={s.strip() for s in LINK.findall(body)} & valid_slugs
    if gt: samples.append((body.strip()[:6000], gt))
print(f"summaries with concept links: {len(samples)}")

# batch-embed all distinct texts (concept lines + queries) once
def embed_all(texts):
    out=[]; B=200
    for i in range(0,len(texts),B):
        r=litellm.embedding(model="text-embedding-3-small", input=texts[i:i+B])
        out += [d["embedding"] for d in r.data]
    return out
print("embedding corpus...")
all_texts = concept_lines + [q for q,_ in samples]
emb = embed_all(all_texts)
line_emb = emb[:len(concept_lines)]
query_emb = {i: emb[len(concept_lines)+i] for i in range(len(samples))}
def embed_fn_cached(texts):  # texts == concept_lines + [query]; reuse cache
    q=texts[-1]; qi=[i for i,(s,_) in enumerate(samples) if s==q]
    return line_emb + [query_emb[qi[0]]]

def slugs_of(blk): return {ln[2:].split(":",1)[0] for ln in blk.splitlines() if ln.startswith("- ")}
def recall(ret, gt): return len(ret & gt)/len(gt)

for K in (20,40):
    rt=[]; re_=[]
    for q,gt in samples:
        rt.append(recall(slugs_of(select_relevant_briefs(q,block,K)), gt))
        re_.append(recall(slugs_of(select_relevant_briefs_embed(q,block,K,embed_fn_cached)), gt))
    print(f"K={K:>2}: TF-IDF recall@K={statistics.mean(rt):.3f} | Embeddings recall@K={statistics.mean(re_):.3f}  "
          f"(prompt: {K}/{len(concept_lines)} briefs = {100*K/len(concept_lines):.0f}% of full)")
