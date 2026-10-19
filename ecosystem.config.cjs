const path = require("path");
const fs = require("fs");

const isWin = process.platform === "win32";
const python = isWin
  ? path.join(__dirname, ".venv", "Scripts", "python.exe")
  : path.join(__dirname, ".venv", "bin", "python");

if (!fs.existsSync(python)) {
  console.error(
    `Missing ${python}\nRun the install script first (scripts/install.sh or scripts/install.ps1).`
  );
  process.exit(1);
}

module.exports = {
  apps: [
    {
      name: "metdet-mesh-bot",
      cwd: __dirname,
      script: python,
      args: "-m bot.main",
      interpreter: "none",
      autorestart: true,
      max_restarts: 40,
      min_uptime: "5s",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
