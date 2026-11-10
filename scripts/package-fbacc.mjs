// Export committed module files only; never package a private working directory.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const ref = process.argv[2] || 'HEAD';
const commit = execFileSync('git', ['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`], { cwd: root, encoding: 'utf8' }).trim();
const prefix = 'services/amazon-fbacc';
const pkg = JSON.parse(execFileSync('git', ['show', `${commit}:${prefix}/package.json`], { cwd: root, encoding: 'utf8' }));
const output = path.join(root, 'dist/packages');
mkdirSync(output, { recursive: true });
const name = `amazon-fbacc-v${pkg.version}-${commit.slice(0, 8)}.tar.gz`;
const archive = path.join(output, name);
execFileSync('git', ['archive', '--format=tar.gz', `--output=${archive}`, `${commit}:${prefix}`], { cwd: root });
const sha256 = createHash('sha256').update(readFileSync(archive)).digest('hex');
writeFileSync(`${archive}.sha256`, `${sha256}  ${name}\n`);
writeFileSync(`${archive}.json`, JSON.stringify({ component: 'fbacc', version: pkg.version, commit, sha256 }, null, 2) + '\n');
console.log(`Exported committed FBACC ${pkg.version} from ${commit}\n${archive}\nSHA-256 ${sha256}`);
