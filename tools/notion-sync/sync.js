#!/usr/bin/env node
// Notion sync tool — pushes worldbuilding/*.md straight to Notion via the official API,
// bypassing the MCP round-trip (which re-sends full page content through the model
// context on every call). Usage:
//
//   node sync.js update <relative-md-path>                     # replace an existing page's content
//   node sync.js create <relative-md-path> --title "..." --icon "🔬" --parent <page_id>
//
// Paths are relative to worldbuilding/. Page IDs are tracked in page-map.json.

const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
const { Client } = require("@notionhq/client");
const { markdownToBlocks } = require("@tryfabric/martian");

const WORLDBUILDING_ROOT = path.join(__dirname, "..", "..", "worldbuilding");
const PAGE_MAP_PATH = path.join(__dirname, "page-map.json");
const APPEND_CHUNK_SIZE = 90; // Notion caps children-append at 100 blocks/call; stay under it

function loadPageMap() {
  if (!fs.existsSync(PAGE_MAP_PATH)) return {};
  return JSON.parse(fs.readFileSync(PAGE_MAP_PATH, "utf8"));
}

function savePageMap(map) {
  fs.writeFileSync(PAGE_MAP_PATH, JSON.stringify(map, null, 2) + "\n");
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

function extractTitle(raw, relPath) {
  const m = raw.match(/^#\s+(.+)$/m);
  if (m) return m[1].trim();
  return path.basename(relPath, ".md");
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
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
  const blocks = markdownToBlocks(processed);

  await replaceChildren(notion, pageId, blocks);
  console.log(`✅ 갱신 완료: ${relPath} -> https://app.notion.com/p/${pageId.replace(/-/g, "")}`);
}

async function cmdCreate(notion, relPath, opts) {
  if (!opts.title || !opts.parent) {
    console.error("create에는 --title과 --parent가 필요합니다 (--icon은 선택).");
    process.exit(1);
  }

  const fullPath = path.join(WORLDBUILDING_ROOT, relPath);
  const raw = fs.readFileSync(fullPath, "utf8");
  const processed = preprocessMarkdown(raw, relPath);
  const blocks = markdownToBlocks(processed);
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
  if (!process.env.NOTION_TOKEN) {
    console.error("NOTION_TOKEN이 .env에 없습니다.");
    process.exit(1);
  }
  const notion = new Client({ auth: process.env.NOTION_TOKEN });
  const { cmd, relPath, opts } = parseArgs(process.argv.slice(2));

  if (!cmd || !relPath) {
    console.error("사용법: node sync.js <update|create> <relative-md-path> [--title X --icon Y --parent Z]");
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
