import type { NextConfig } from "next";

const nextConfig: NextConfig = {
    output: 'standalone',
    async rewrites() {
        const target = process.env.API_INTERNAL_URL || 'http://twaky-api:8000'
        return [
            { source: '/api/:path*', destination: `${target}/:path*` },
            { source: '/oauth/:path*', destination: `${target}/oauth/:path*` },
        ]
    },
};

export default nextConfig;
