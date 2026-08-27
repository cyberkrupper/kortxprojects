'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const HOST = '127.0.0.1';
const ROOT = __dirname;
const HTML_PATH = path.join(ROOT, 'VMR.html');
const DATA_PATH = path.join(ROOT, 'vmr-data.json');
const MAX_DATA_BYTES = 10 * 1024 * 1024;

function send(res, status, body, contentType = 'text/plain; charset=utf-8') {
  res.writeHead(status, {
    'Content-Type': contentType,
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'X-VMR-Local-Server': '1'
  });
  res.end(body);
}

function serveFile(res, filePath, contentType) {
  fs.readFile(filePath, (error, body) => {
    if (error) {
      send(res, error.code === 'ENOENT' ? 404 : 500, error.code === 'ENOENT' ? 'File not found.' : 'Could not read file.');
      return;
    }
    res.writeHead(200, {
      'Content-Type': contentType,
      'Content-Length': body.length,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'X-VMR-Local-Server': '1'
    });
    res.end(body);
  });
}

function saveData(req, res) {
  const chunks = [];
  let size = 0;

  req.on('data', chunk => {
    size += chunk.length;
    if (size > MAX_DATA_BYTES) {
      send(res, 413, 'Data file is too large.');
      req.destroy();
      return;
    }
    chunks.push(chunk);
  });

  req.on('end', () => {
    if (size > MAX_DATA_BYTES) return;
    const json = Buffer.concat(chunks).toString('utf8');
    try {
      const parsed = JSON.parse(json);
      if (!parsed || !Array.isArray(parsed.vehicles)) throw new Error('Expected a VMR vehicle list.');
    } catch (error) {
      send(res, 400, 'Invalid VMR JSON: ' + error.message);
      return;
    }

    const temporaryPath = DATA_PATH + '.tmp';
    fs.writeFile(temporaryPath, json, 'utf8', error => {
      if (error) {
        send(res, 500, 'Could not write the temporary data file.');
        return;
      }
      fs.rename(temporaryPath, DATA_PATH, renameError => {
        if (renameError) {
          fs.rm(temporaryPath, { force: true }, () => {});
          send(res, 500, 'Could not replace vmr-data.json.');
          return;
        }
        send(res, 200, 'Saved.');
      });
    });
  });

  req.on('error', () => {
    if (!res.headersSent) send(res, 400, 'The save request was interrupted.');
  });
}

const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, `http://${HOST}`).pathname;

  if (req.method === 'GET' && (pathname === '/' || pathname === '/VMR.html')) {
    serveFile(res, HTML_PATH, 'text/html; charset=utf-8');
    return;
  }
  if (req.method === 'GET' && pathname === '/vmr-data.json') {
    serveFile(res, DATA_PATH, 'application/json; charset=utf-8');
    return;
  }
  if (req.method === 'PUT' && pathname === '/vmr-data.json') {
    saveData(req, res);
    return;
  }
  send(res, 404, 'Not found.');
});

server.on('error', error => {
  console.error('VMR could not start:', error.message);
  process.exitCode = 1;
});

server.listen(0, HOST, () => {
  const { port } = server.address();
  const url = `http://${HOST}:${port}/VMR.html`;
  console.log('VMR is running at ' + url);
  console.log('Keep this window open while using VMR. Press Ctrl+C to stop.');

  if (!process.argv.includes('--no-open')) {
    const opener = spawn('cmd.exe', ['/c', 'start', '', url], { detached: true, stdio: 'ignore', windowsHide: true });
    opener.unref();
  }
});

process.on('SIGINT', () => server.close(() => process.exit(0)));
