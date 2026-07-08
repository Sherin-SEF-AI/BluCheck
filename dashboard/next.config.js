/** @type {import('next').NextConfig} */
// Static export: the dashboard is hosted as static assets on S3 behind CloudFront and
// talks to the API at runtime. No server-side rendering.
const nextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

module.exports = nextConfig;
