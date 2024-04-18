import { spawn } from 'child_process'
import path from 'path'
import { $ } from "bun";
import { platform } from 'os';

try {
    await $`rm -rf build`
    await $`rm -rf dist`
    await $`rm umdapy.spec`    
} catch (error) {
    console.log('No build or dist directory')
}


const maindir = path.resolve("../src")
const icon = path.join(maindir, 'icons/icon.ico')
const hooks = path.join(maindir, 'hooks')
const mainfile = path.join(maindir, 'main.py')

// const target_arch = platform() === 'darwin' ? '--target-architecture x86_64 --target-architecture arm64' : ''
// const target_arch = platform() === 'darwin' ? '--target-architecture universal2' : ''
// const target_arch = platform() === 'darwin' ? '--target-architecture x86_64' : ''
// console.log(platform(), target_arch)

const args =
    `--noconfirm --onedir --console --icon ${icon} --name umdapy --debug noarchive --noupx --additional-hooks-dir ${hooks} --hidden-import umdalib --paths ${maindir} ${mainfile}`.split(
        ' '
    )

console.log(args)

const py = spawn("pyinstaller", args)
py.stdout.on('data', (data) => console.log(data.toString('utf8')))
py.stderr.on('data', (data) => console.log(data.toString('utf8')))
py.on('close', async () => {
    console.log('pyinstaller done')
    await $`cd dist && zip -r9 umdapy-darwin.zip umdapy/`
})
py.on('error', (err) => console.log('error occured', err))
