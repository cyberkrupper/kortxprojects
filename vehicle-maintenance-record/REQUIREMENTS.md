# Requirements and Installation

## Runtime requirements

- Node.js 18 or newer for the recommended local launcher.
- A modern browser with JavaScript enabled.
- Windows for the double-click `OPEN_VMR.bat` shortcut.
- Git only when cloning or contributing; downloaded release archives do not need it.

VMR has no third-party npm packages, database, build tool, web server, or cloud account requirement. Do not run `npm install`; there is nothing to download for the application itself.

## Check what is installed

In Bash:

```bash
node --version
npm --version
git --version
```

In PowerShell:

```powershell
node --version
npm.cmd --version
git --version
```

If `node` or `npm` is not found, install a current Node.js LTS release from [nodejs.org](https://nodejs.org/) or through your operating system's trusted package manager. Node.js includes npm. Git is available from [git-scm.com](https://git-scm.com/).

## Clone and run from Bash

```bash
git clone https://github.com/cyberkrupper/kortxprojects.git
cd kortxprojects/vehicle-maintenance-record
npm run check
npm run start:no-open
```

Open the `http://127.0.0.1:.../VMR.html` address printed in the terminal. On first use, VMR asks you to create or select `vmr-data.json`.

## Clone and run from PowerShell

```powershell
git clone https://github.com/cyberkrupper/kortxprojects.git
Set-Location kortxprojects\vehicle-maintenance-record
npm.cmd run check
npm.cmd start
```

On Windows, `OPEN_VMR.bat` is equivalent to starting the launcher and opening the app.

## Browser-only option

Node.js is not required if you open `VMR.html` directly. Browser-only storage and file access vary by browser, so the Node.js launcher is recommended for reliable automatic saving.
