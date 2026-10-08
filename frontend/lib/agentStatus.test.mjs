import assert from "node:assert/strict";
import test from "node:test";
import { agentConfigurationLabel } from "./agentStatus.ts";

test("configured model is not labelled remotely available", () => {
  assert.equal(agentConfigurationLabel({ llm_configured: true, model: "deepseek-flash", provider: "deepseek" }), "deepseek-flash 已配置");
});
test("missing credentials are visible even when a model name is set", () => {
  assert.equal(agentConfigurationLabel({ llm_configured: false, model: "deepseek-flash", provider: "deepseek" }), "LLM 未配置");
});
test("extractive mode is not mistaken for a generative model", () => {
  assert.equal(agentConfigurationLabel({ llm_configured: true, model: null, provider: "extractive" }), "原文整理模式");
});
test("configured provider without a model uses a neutral label", () => {
  assert.equal(agentConfigurationLabel({ llm_configured: true, model: null, provider: "custom" }), "LLM 已配置");
});
