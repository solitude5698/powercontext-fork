import { createReadStream, existsSync } from 'node:fs';
import { join } from 'node:path';
import { defineConfig } from 'vite';

// Vite dev appends `?import` when onnxruntime-web dynamically imports the
// emscripten glue from /public, which the transform pipeline rejects.
// Serve those loader files verbatim so dev mode matches `vite preview`.
function serveOrtLoaders() {
  return {
    name: 'serve-ort-loaders',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const [pathname] = (req.url ?? '').split('?');
        if (!pathname.startsWith('/ort/') || !pathname.endsWith('.mjs')) return next();
        const file = join(server.config.publicDir, pathname);
        if (!existsSync(file)) return next();
        res.setHeader('Content-Type', 'text/javascript; charset=utf-8');
        createReadStream(file).pipe(res);
      });
    },
  };
}

export default defineConfig({
  plugins: [serveOrtLoaders()],
});
