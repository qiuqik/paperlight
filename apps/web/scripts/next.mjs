import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {dirname, join} from 'node:path';

const require = createRequire(import.meta.url);
const [command, ...args] = process.argv.slice(2);
if (!['dev', 'build'].includes(command)) throw new Error('Expected dev or build');

const env = {...process.env};
if (process.platform === 'win32') {
  try {
    require('@next/swc-win32-x64-msvc');
  } catch {
    // Some Windows installations cannot load the native SWC binary.
    env.NEXT_TEST_WASM_DIR = dirname(require.resolve('@next/swc-wasm-nodejs/wasm.js'));
    env.NEXT_SWC_PATH = join(process.cwd(), '.next-swc-cache');
  }
}

const child = spawn(process.execPath, [require.resolve('next/dist/bin/next'), command, '--webpack', ...args], {env, stdio: 'inherit'});
child.on('exit', code => {process.exitCode = code ?? 1;});
