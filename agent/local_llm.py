"""Local LLM wrapper: transformers + torch CPU, no cloud API involved.

Design notes
------------
* Fully offline inference: weights are read from a local directory, and the
  process never opens a network socket.
* Every generation returns a metrics dict so the agent trace can record real
  prefill/decode timings instead of guessed numbers.
* Source file is ASCII-only; Chinese content lives in markdown/data files.
"""
import json
import os
import time

import psutil
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

DEFAULTS = {
    "max_new_tokens": 192,
    "temperature": 0.0,
    "repetition_penalty": 1.05,
    "context_limit": 3072,
}


class LocalLLM:
    def __init__(self, model_dir, threads=None, quant="none", verbose=False):
        self.model_dir = model_dir
        self.quant = quant
        self.verbose = verbose
        if threads:
            torch.set_num_threads(int(threads))
        self.threads = torch.get_num_threads()
        t0 = time.time()
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_dir, dtype=torch.float32, low_cpu_mem_usage=True,
            local_files_only=True,
        )
        self.model.eval()
        if quant == "int8":
            try:
                self.model = torch.ao.quantization.quantize_dynamic(
                    self.model, {torch.nn.Linear}, dtype=torch.qint8
                )
            except Exception as exc:  # pragma: no cover - environment dependent
                self.quant = "none(int8-failed:%s)" % str(exc)[:40]
        self.load_seconds = round(time.time() - t0, 2)
        self.rss_mb = round(psutil.Process().memory_info().rss / 1e6, 1)
        self.calls = 0
        self.gen_tokens_total = 0
        self.gen_seconds_total = 0.0

    # ---------------------------------------------------------------- helpers
    def count_tokens(self, text):
        return len(self.tokenizer(text, add_special_tokens=False)["input_ids"])

    def _render(self, messages):
        return self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

    def info(self):
        return {
            "model_dir": os.path.basename(os.path.normpath(self.model_dir)),
            "quant": self.quant,
            "threads": self.threads,
            "load_seconds": self.load_seconds,
            "rss_mb": self.rss_mb,
            "calls": self.calls,
            "gen_tokens": self.gen_tokens_total,
            "gen_seconds": round(self.gen_seconds_total, 3),
        }

    # ------------------------------------------------------------- inference
    def chat(self, messages, max_new_tokens=None, temperature=None, stop=None):
        max_new_tokens = max_new_tokens or DEFAULTS["max_new_tokens"]
        temperature = DEFAULTS["temperature"] if temperature is None else temperature
        text = self._render(messages)
        enc = self.tokenizer(text, return_tensors="pt", add_special_tokens=False)
        prompt_len = enc["input_ids"].shape[1]
        limit = DEFAULTS["context_limit"]
        if prompt_len > limit:
            # keep the tail of the prompt (system + latest turns win)
            enc = {
                "input_ids": enc["input_ids"][:, -limit:],
                "attention_mask": enc["attention_mask"][:, -limit:],
            }
            prompt_len = limit
        do_sample = temperature and temperature > 0
        kwargs = dict(
            max_new_tokens=max_new_tokens,
            do_sample=bool(do_sample),
            pad_token_id=self.tokenizer.eos_token_id,
            repetition_penalty=DEFAULTS["repetition_penalty"],
        )
        if do_sample:
            kwargs["temperature"] = float(temperature)
        t0 = time.time()
        with torch.no_grad():
            out = self.model.generate(**enc, **kwargs)
        elapsed = time.time() - t0
        new_ids = out[0][prompt_len:]
        raw = self.tokenizer.decode(new_ids, skip_special_tokens=True)
        gen_tokens = int(len(new_ids))
        if stop:
            for marker in stop:
                idx = raw.find(marker)
                if idx >= 0:
                    raw = raw[:idx]
        self.calls += 1
        self.gen_tokens_total += gen_tokens
        self.gen_seconds_total += elapsed
        metrics = {
            "prompt_tokens": int(prompt_len),
            "gen_tokens": gen_tokens,
            "seconds": round(elapsed, 3),
            "tokens_per_s": round(gen_tokens / elapsed, 2) if elapsed > 0 else 0.0,
            "rss_mb": round(psutil.Process().memory_info().rss / 1e6, 1),
        }
        return raw, metrics


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
