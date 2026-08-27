# VMR User Guide

## Starting the app

On Windows, double-click `OPEN_VMR.bat`. The launcher starts a local server and opens VMR in your default browser. On first use, follow the prompt to create or select `vmr-data.json`. Keep the terminal window open; closing it stops the app.

Alternatively, run `npm start` from the project directory. Node.js 18 or newer is required, but no packages need to be installed.

## Adding a vehicle

Choose **Add vehicle**, then enter at least the registration plate and model. Year, current mileage, and VIN are optional. Each plate or VIN must be unique.

Open a vehicle card to:

- update its current mileage;
- add documents and their issue or expiry dates;
- record maintenance, cost, mileage, and repeat interval;
- record fuel quantity, cost, odometer value, and whether it was a full tank;
- review upcoming maintenance and expenses.

VMR uses `km` for distance and `RON` for money in the current interface.

## How saving works

When started with the launcher, VMR loads and saves `vmr-data.json` automatically. The header shows the current save state. Wait until it says **Saved locally** before closing the browser or terminal.

When `VMR.html` is opened directly, browser capabilities vary:

- supported browsers can link a selected JSON file and write changes to it;
- other browsers keep the working copy in local browser storage and require manual JSON export for a disk backup.

Do not edit `vmr-data.json` while VMR is open. A malformed file or a vehicle without both a plate and model will be rejected.

## Backup and restore

Use the data-file control in the header and choose **Export backup**. Keep backup copies somewhere protected and separate from the application folder.

To restore in browser-only mode, use **Import backup** and select a previous `vmr-data.json`. Import replaces the current browser data, so export the current data first if it may still be needed.

With the launcher stopped, you can restore by placing a valid backup in the application directory and naming it `vmr-data.json`. Preserve the existing file under another name before replacing it.

## Updating VMR

Before replacing application files or pulling a newer release:

1. Export a backup.
2. Stop the launcher with `Ctrl+C`.
3. Update the application files.
4. Keep your existing `vmr-data.json` in the project directory.
5. Start VMR and verify that the vehicle list loads.

Because `vmr-data.json` is ignored by Git, normal pulls do not overwrite it.

## Troubleshooting

### Node.js was not found

Install Node.js 18 or newer, close and reopen the terminal, then run `node --version` to confirm it is available.

### The page opened but no data loaded

If this is the first launch, use the prompt to create a data file. Otherwise, confirm that `vmr-data.json` is beside `VMR.html` and contains valid JSON, then stop and restart the launcher.

### Changes are not reaching the file

Check the save indicator. Keep the launcher window open and use the `http://127.0.0.1:...` page it opened, rather than a separate `file://` copy of `VMR.html`.

### The port is already in use

The launcher automatically selects a free local port. If startup still fails, close old VMR terminal windows and start it again.
