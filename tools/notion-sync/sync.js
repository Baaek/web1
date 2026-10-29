#!/usr/bin/env node
// Notion sync tool — pushes worldbuilding/*.md straight to Notion via the official API,
// bypassing the MCP round-trip (which re-sends full page content through the model
// context on every call). Usage:
//
//   node sync.js update <relative-md-path>                     # replace an existing page's content
//   node sync.js update-all                                    # replace all pages tracked in page-map.json
//   node sync.js create <relative-md-path> --title "..." --icon "🔬" --parent <page_id>
//   node sync.js onboard-all                                   # create Notion pages for every .md not yet tracked
//
// Paths are relative to worldbuilding/. Page IDs are tracked in page-map.json.
// Folder pages created by onboard-all (mirroring worldbuilding/'s subdirectories)
// are tracked separately in folder-map.json, keyed by the directory's relative path.

const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
const { Client } = require("@notionhq/client");
const { markdownToBlocks } = require("@tryfabric/martian");
const { HttpsProxyAgent } = require("https-proxy-agent");

const WORLDBUILDING_ROOT = path.join(__dirname, "..", "..", "worldbuilding");
const PAGE_MAP_PATH = path.join(__dirname, "page-map.json");
const FOLDER_MAP_PATH = path.join(__dirname, "folder-map.json");
const APPEND_CHUNK_SIZE = 90; // Notion caps children-append at 100 blocks/call; stay under it
// Parent of the 7 pre-existing top-level pages ("🌍 요르문간드 연대기 위키"). New top-level
// docs nest directly under it too; onboard-all creates folder pages under it for subdirectories.
const ROOT_PARENT_ID = "3c1f0578-920b-8115-8d16-dcd5306ef2f4";

function loadJsonMap(mapPath) {
  if (!fs.existsSync(mapPath)) return {};
  return JSON.parse(fs.readFileSync(mapPath, "utf8"));
}

function saveJsonMap(mapPath, map) {
  fs.writeFileSync(mapPath, JSON.stringify(map, null, 2) + "\n");
}

function loadPageMap() {
  return loadJsonMap(PAGE_MAP_PATH);
}

function savePageMap(map) {
  saveJsonMap(PAGE_MAP_PATH, map);
}

function loadFolderMap() {
  return loadJsonMap(FOLDER_MAP_PATH);
}

function saveFolderMap(map) {
  saveJsonMap(FOLDER_MAP_PATH, map);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Recursively lists every .md file under worldbuilding/, as POSIX-style paths
// relative to it (matching the keys already used in page-map.json).
function walkMdFiles(dir, base) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === ".git") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkMdFiles(full, base));
    else if (entry.name.endsWith(".md")) out.push(path.relative(base, full).split(path.sep).join("/"));
  }
  return out;
}

// Ensures a Notion "folder" page exists for dirRelPath (e.g. "characters/백-정"),
// creating parent folders first as needed, and returns its page id.
async function getOrCreateFolder(notion, folderMap, dirRelPath) {
  if (dirRelPath === "." || dirRelPath === "") return ROOT_PARENT_ID;
  if (folderMap[dirRelPath]) return folderMap[dirRelPath];

  const parentDir = path.posix.dirname(dirRelPath);
  const parentId = await getOrCreateFolder(notion, folderMap, parentDir);
  const title = path.posix.basename(dirRelPath);

  const page = await notion.pages.create({
    parent: { type: "page_id", page_id: parentId },
    icon: { type: "emoji", emoji: "📁" },
    properties: { title: [{ text: { content: title } }] },
  });
  folderMap[dirRelPath] = page.id;
  saveFolderMap(folderMap);
  console.log(`📁 폴더 생성: ${dirRelPath}`);
  await sleep(300);
  return page.id;
}

// Mirrors the manual preprocessing used for the first sync round:
// strip the leading H1 (title goes in page properties instead), turn relative
// repo links into bold plain text (Notion pages don't resolve ./ or events/ links),
// and prepend a banner noting this is a git mirror.
function preprocessMarkdown(raw, relPath) {
  let text = raw.replace(/\r\n/g, "\n");

  const lines = text.split("\n");
  let firstNonEmpty = lines.findIndex((l) => l.trim() !== "");
  if (firstNonEmpty !== -1 && /^#\s+/.test(lines[firstNonEmpty])) {
    lines.splice(firstNonEmpty, 1);
    text = lines.join("\n");
  }

  // [text](./foo.md) or [text](events/foo.md) -> **text** (skip real URLs)
  text = text.replace(/\[([^\]]+)\]\((?!https?:\/\/)[^)]*\)/g, (_, inner) => {
    const stripped = inner.replace(/^\*\*/, "").replace(/\*\*$/, "");
    return `**${stripped}**`;
  });

  const today = new Date().toISOString().slice(0, 10);
  const banner = `> 📌 git \`worldbuilding/${relPath}\`의 미러입니다. **정본은 git이며**, 이 페이지는 ${today} 기준입니다.\n\n`;
  return banner + text.trimStart();
}

// Notion's pages.create/blocks.append reject a payload where a block's children
// are themselves nested more than ~2 levels deep in one call ("children should be
// not present"). Deeply nested markdown lists (4+ levels) hit this, so flatten
// anything past depth 2 into siblings at depth 2 instead of failing the whole page.
function capNestingDepth(blocks) {
  function flatten(nodes) {
    const out = [];
    for (const block of nodes) {
      const content = block[block.type];
      if (content && Array.isArray(content.children) && content.children.length) {
        const children = content.children;
        delete content.children;
        out.push(block, ...flatten(children));
      } else {
        out.push(block);
      }
    }
    return out;
  }

  // nodes here is the array a single depth occupies; when a node at depth 2 has
  // its own children, those must become siblings *within this same array* (not
  // nested one level deeper inside the node), or the depth violation just moves down one.
  function cap(nodes, depth) {
    const out = [];
    for (const block of nodes) {
      const content = block[block.type];
      if (content && Array.isArray(content.children) && content.children.length) {
        if (depth < 2) {
          content.children = cap(content.children, depth + 1);
          out.push(block);
        } else {
          const kids = content.children;
          delete content.children;
          out.push(block, ...flatten(kids));
        }
      } else {
        out.push(block);
      }
    }
    return out;
  }

  return cap(blocks, 0);
}

function toNotionBlocks(processed) {
  return capNestingDepth(markdownToBlocks(processed));
}

function extractTitle(raw, relPath) {
  const m = raw.match(/^#\s+(.+)$/m);
  if (m) return m[1].trim();
  return path.basename(relPath, ".md");
}

// Groups blocks respecting both a max count (Notion's 100-blocks/call cap) and a max
// combined JSON byte size (Notion's ~500KB request body cap) — a chunk of 90 small
// blocks is fine, but 90 blocks that happen to be large (long paragraphs/tables) can
// blow past the byte limit and get rejected with 413 even though the count is legal.
const MAX_CHUNK_BYTES = 350_000;

function chunk(arr, size) {
  const out = [];
  let current = [];
  let currentBytes = 0;
  for (const item of arr) {
    const itemBytes = JSON.stringify(item).length;
    if (current.length > 0 && (current.length >= size || currentBytes + itemBytes > MAX_CHUNK_BYTES)) {
      out.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(item);
    currentBytes += itemBytes;
  }
  if (current.length > 0) out.push(current);
  return out;
}

async function replaceChildren(notion, pageId, blocks) {
  // Delete existing children (Notion has no bulk-replace endpoint).
  let cursor;
  const existing = [];
  do {
    const res = await notion.blocks.children.list({ block_id: pageId, start_cursor: cursor });
    existing.push(...res.results);
    cursor = res.has_more ? res.next_cursor : undefined;
  } while (cursor);

  for (const block of existing) {
    await notion.blocks.delete({ block_id: block.id });
  }

  for (const part of chunk(blocks, APPEND_CHUNK_SIZE)) {
    await notion.blocks.children.append({ block_id: pageId, children: part });
  }
}

async function cmdUpdate(notion, relPath) {
  const map = loadPageMap();
  const pageId = map[relPath];
  if (!pageId) {
    console.error(`page-map.json에 "${relPath}" 항목이 없습니다. 먼저 create로 생성하세요.`);
    process.exit(1);
  }

  const fullPath = path.join(WORLDBUILDING_ROOT, relPath);
  const raw = fs.readFileSync(fullPath, "utf8");
  const processed = preprocessMarkdown(raw, relPath);
  const blocks = toNotionBlocks(processed);

  await replaceChildren(notion, pageId, blocks);
  console.log(`✅ 갱신 완료: ${relPath} -> https://app.notion.com/p/${pageId.replace(/-/g, "")}`);
}

async function cmdUpdateAll(notion) {
  const map = loadPageMap();
  const relPaths = Object.keys(map);
  if (relPaths.length === 0) {
    console.log("page-map.json이 비어 있습니다. 갱신할 페이지가 없습니다.");
    return;
  }

  const failed = [];
  for (const relPath of relPaths) {
    try {
      await cmdUpdate(notion, relPath);
    } catch (err) {
      failed.push(relPath);
      console.error(`❌ ${relPath} 갱신 실패:`, err.body || err.message || err);
    }
  }

  console.log(`\n총 ${relPaths.length}개 중 ${relPaths.length - failed.length}개 성공, ${failed.length}개 실패.`);
  if (failed.length > 0) {
    console.error("실패 목록:", failed.join(", "));
    process.exitCode = 1;
  }
}

async function cmdOnboardAll(notion) {
  const pageMap = loadPageMap();
  const folderMap = loadFolderMap();
  const allFiles = walkMdFiles(WORLDBUILDING_ROOT, WORLDBUILDING_ROOT);
  const todo = allFiles.filter((relPath) => !pageMap[relPath]);

  if (todo.length === 0) {
    console.log("새로 만들 문서가 없습니다 — 전부 page-map.json에 이미 있음.");
    return;
  }
  console.log(`총 ${todo.length}개 신규 문서를 생성합니다 (전체 ${allFiles.length}개 중).`);

  let ok = 0;
  const failed = [];
  for (const relPath of todo) {
    try {
      const dir = path.posix.dirname(relPath);
      const parentId = await getOrCreateFolder(notion, folderMap, dir);

      const fullPath = path.join(WORLDBUILDING_ROOT, relPath);
      const raw = fs.readFileSync(fullPath, "utf8");
      const processed = preprocessMarkdown(raw, relPath);
      const blocks = toNotionBlocks(processed);
      const parts = chunk(blocks, APPEND_CHUNK_SIZE);
      const title = extractTitle(raw, relPath);

      const page = await notion.pages.create({
        parent: { type: "page_id", page_id: parentId },
        properties: { title: [{ text: { content: title } }] },
        children: parts[0] || [],
      });
      for (const part of parts.slice(1)) {
        await notion.blocks.children.append({ block_id: page.id, children: part });
        await sleep(300);
      }

      pageMap[relPath] = page.id;
      savePageMap(pageMap);
      ok++;
      console.log(`✅ (${ok}/${todo.length}) 생성: ${relPath}`);
      await sleep(300);
    } catch (err) {
      failed.push(relPath);
      console.error(`❌ ${relPath} 생성 실패:`, err.body || err.message || err);
    }
  }

  console.log(`\n총 ${todo.length}개 중 ${ok}개 성공, ${failed.length}개 실패.`);
  if (failed.length > 0) {
    console.error("실패 목록:", failed.join(", "));
    process.exitCode = 1;
  }
}

async function cmdCreate(notion, relPath, opts) {
  if (!opts.title || !opts.parent) {
    console.error("create에는 --title과 --parent가 필요합니다 (--icon은 선택).");
    process.exit(1);
  }

  const fullPath = path.join(WORLDBUILDING_ROOT, relPath);
  const raw = fs.readFileSync(fullPath, "utf8");
  const processed = preprocessMarkdown(raw, relPath);
  const blocks = toNotionBlocks(processed);
  const parts = chunk(blocks, APPEND_CHUNK_SIZE);

  const page = await notion.pages.create({
    parent: { type: "page_id", page_id: opts.parent },
    icon: opts.icon ? { type: "emoji", emoji: opts.icon } : undefined,
    properties: { title: [{ text: { content: opts.title } }] },
    children: parts[0] || [],
  });

  for (const part of parts.slice(1)) {
    await notion.blocks.children.append({ block_id: page.id, children: part });
  }

  const map = loadPageMap();
  map[relPath] = page.id;
  savePageMap(map);

  console.log(`✅ 생성 완료: ${relPath} -> ${page.url}`);
}

function parseArgs(argv) {
  const [cmd, relPath, ...rest] = argv;
  const opts = {};
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i].replace(/^--/, "");
    opts[key] = rest[i + 1];
  }
  return { cmd, relPath, opts };
}

async function main() {
  // No auth token passed here on purpose: this environment's API-credential
  // feature injects the Authorization header for api.notion.com at the proxy
  // level, so the token never needs to live in this process or in a file.
  // If NOTION_TOKEN is set (e.g. running locally without that proxy), use it.
  // node-fetch (used internally by @notionhq/client) doesn't read HTTPS_PROXY
  // on its own, so route it through the agent proxy explicitly when present —
  // that's also where the injected credential is attached.
  const proxyUrl = process.env.HTTPS_PROXY || process.env.https_proxy;
  const notion = new Client({
    ...(process.env.NOTION_TOKEN ? { auth: process.env.NOTION_TOKEN } : {}),
    ...(proxyUrl ? { agent: new HttpsProxyAgent(proxyUrl) } : {}),
  });
  const { cmd, relPath, opts } = parseArgs(process.argv.slice(2));

  if (cmd === "update-all") {
    await cmdUpdateAll(notion);
    return;
  }
  if (cmd === "onboard-all") {
    await cmdOnboardAll(notion);
    return;
  }

  if (!cmd || !relPath) {
    console.error(
      "사용법: node sync.js <update|create> <relative-md-path> [--title X --icon Y --parent Z] | node sync.js update-all | node sync.js onboard-all"
    );
    process.exit(1);
  }

  if (cmd === "update") await cmdUpdate(notion, relPath);
  else if (cmd === "create") await cmdCreate(notion, relPath, opts);
  else {
    console.error(`알 수 없는 명령: ${cmd}`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("❌ 실패:", err.body || err.message || err);
  process.exit(1);
});
