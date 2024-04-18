import { spawn } from 'child_process'
import path from 'path'
import { $ } from "bun";

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

// const site_pkgs = path.resolve("/Users/aravindhnivas/anaconda3/envs/umdapy/lib/python3.9/site-packages/")
// const distributed = path.join(site_pkgs, "distributed/distributed.yaml")
// const dask = path.join(site_pkgs, "dask/dask.yaml")

// const misc = `--hidden-import wandb --hidden-import wandb_gql --add-data ${dask};./dask --add-data ${distributed};./distributed`

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
