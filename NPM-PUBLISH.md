# Publishing rlm-navigator to npm

## Prerequisites

1. **Create an npm account** at https://www.npmjs.com/signup
2. **Node.js 18+** installed

## Steps

### 1. Log in to npm from the CLI

```bash
npm login
```

Follow the prompts — email, password, OTP if 2FA is enabled. Verify with:

```bash
npm whoami
```

### 2. Verify the package name is available

```bash
npm view rlm-navigator
```

Should return a 404 / "not found" error — meaning the name is free.

### 3. Dry-run to confirm tarball contents

```bash
cd /e/codebase/rlm-navigator
npm pack --dry-run
```

Confirm you see only the 12 expected files (bin/, daemon/*.py, server/src, templates, etc). No `__pycache__`, no tests.

### 4. Publish

```bash
npm publish
```

Since this is the first publish and the package name has no scope, it publishes as a public package by default.

If you get a 403 about the name being too similar to an existing package, you have two options:
- **Scoped package**: Change `name` in `package.json` to `@yourname/rlm-navigator`, then run `npm publish --access public`
- **Different name**: Pick a unique name

### 5. Verify the publish

```bash
npm view rlm-navigator
```

Should now show version `0.1.0` with your metadata.

### 6. Test the install flow end-to-end

```bash
cd /tmp
mkdir test-project && cd test-project
git init
npx rlm-navigator@latest help
```

Confirm the help text prints. If you want to do a full install test (requires Python + Claude CLI):

```bash
npx rlm-navigator@latest install
```

### 7. Future releases

When pushing updates:

1. Bump the version: `npm version patch` (or `minor` / `major`)
2. Rebuild the server if `index.ts` changed: `cd server && npm run build`
3. Publish: `npm publish`

The `npm version` command auto-creates a git commit and tag. Push both:

```bash
git push && git push --tags
```
