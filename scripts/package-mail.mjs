// Export only committed public module files, never private mailbox data/configuration.
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const ref = process.argv[2] || 'HEAD';
const commit = execFileSync('git', ['rev-parse', '--verify', '--end-of-options', `${ref}^{commit}`], { cwd: root, encoding: 'utf8' }).trim();
const prefix = 'services/store-mail-web';
const versions = JSON.parse(execFileSync('git', ['show', `${commit}:versions.json`], { cwd: root, encoding: 'utf8' }));
const version = versions.components.mail.version;
const output = path.join(root, 'dist/packages');
mkdirSync(output, { recursive: true });
const name = `store-mail-web-v${version}-${commit.slice(0, 8)}.tar.gz`;
const archive = path.join(output, name);
execFileSync('git', ['archive', '--format=tar.gz', `--output=${archive}`, `${commit}:${prefix}`], { cwd: root });
const sha256 = createHash('sha256').update(readFileSync(archive)).digest('hex');
writeFileSync(`${archive}.sha256`, `${sha256}  ${name}\n`);
writeFileSync(`${archive}.json`, JSON.stringify({ component: 'mail', version, commit, sha256 }, null, 2) + '\n');
console.log(`Exported cloud-mail ${version} from ${commit}\n${archive}\nSHA-256 ${sha256}`);
