FROM node:22-alpine
WORKDIR /app
COPY --chown=node:node package.json ./
COPY --chown=node:node src ./src
COPY --chown=node:node rates ./rates
COPY --chown=node:node public ./public
USER node
ENV FBA_CALCULATOR_HOST=0.0.0.0 FBA_CALCULATOR_PORT=4318
EXPOSE 4318
CMD ["node", "src/server.mjs"]
