"""
LLM Judge for WhisperLive Quality Assessment

Uses large language models to evaluate transcript quality against golden references
with structured rubrics for factual fidelity, omissions, insertions, and overall utility.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any
import hashlib
import time

logger = logging.getLogger(__name__)


@dataclass
class JudgeScore:
    """LLM judge scoring results."""
    conn_id: str
    sample_id: str
    
    # Rubric scores (0-5 scale)
    fidelity: float = 0.0  # Semantic agreement with reference
    omissions: float = 0.0  # Missing content (lower is better)
    insertions: float = 0.0  # Hallucinated content (lower is better)
    overall: float = 0.0  # Overall utility
    
    # Normalized scores (0-1 scale)
    fidelity_norm: float = 0.0
    omissions_norm: float = 0.0
    insertions_norm: float = 0.0
    overall_norm: float = 0.0
    
    # Composite score (0-1, higher is better)
    composite_score: float = 0.0
    
    # Rationale
    rationale: str = ""
    
    # Raw response
    raw_response: str = ""


class LLMJudge:
    """LLM-based quality judge for transcript evaluation."""
    
    def __init__(self, 
                 provider: str = "openai",
                 model: str = "gpt-4o-mini",
                 api_key: Optional[str] = None,
                 cache_dir: Optional[str] = None):
        
        self.provider = provider.lower()
        self.model = model
        self.api_key = api_key or os.getenv(f"{provider.upper()}_API_KEY")
        
        # Caching
        self.cache_dir = Path(cache_dir) if cache_dir else Path("cache/judge")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize provider client
        self.client = self._initialize_client()
        
        # Judge prompt template
        self.judge_prompt = self._load_judge_prompt()
        
    def _initialize_client(self):
        """Initialize the LLM client based on provider."""
        if self.provider == "openai":
            try:
                import openai
                return openai.OpenAI(api_key=self.api_key)
            except ImportError:
                logger.error("OpenAI client not available. Install with: pip install openai")
                return None
        elif self.provider == "anthropic":
            try:
                import anthropic
                return anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                logger.error("Anthropic client not available. Install with: pip install anthropic")
                return None
        else:
            logger.error(f"Unsupported provider: {self.provider}")
            return None
            
    def _load_judge_prompt(self) -> str:
        """Load the judge prompt template."""
        default_prompt = """You are evaluating a transcript against a golden reference for speech recognition quality.

Score each aspect on a 0-5 scale:

**FIDELITY (0-5)**: How well does the transcript capture the semantic meaning of the reference?
- 5: Perfect semantic match, all key information preserved
- 4: Very good match with minor semantic differences
- 3: Good match with some semantic variations
- 2: Partial match with significant semantic differences
- 1: Poor match with major semantic differences
- 0: No semantic relationship

**OMISSIONS (0-5)**: How much content is missing from the reference? (Lower is better)
- 0: No missing content
- 1: Minor omissions (single words/phrases)
- 2: Some omissions (multiple words/phrases)
- 3: Moderate omissions (sentences or concepts)
- 4: Major omissions (significant content)
- 5: Most content missing

**INSERTIONS (0-5)**: How much hallucinated content is added? (Lower is better)
- 0: No insertions
- 1: Minor insertions (single words)
- 2: Some insertions (multiple words)
- 3: Moderate insertions (phrases or sentences)
- 4: Major insertions (significant content)
- 5: Mostly hallucinated content

**OVERALL (0-5)**: Overall utility of the transcript for understanding the content
- 5: Excellent, fully usable
- 4: Very good, minor issues
- 3: Good, some limitations
- 2: Fair, significant limitations
- 1: Poor, major limitations
- 0: Unusable

Provide a 1-line rationale for your scoring.

Return ONLY valid JSON in this exact format:
{
  "fidelity": <score>,
  "omissions": <score>,
  "insertions": <score>,
  "overall": <score>,
  "rationale": "<explanation>"
}"""
        
        return default_prompt
        
    def _get_cache_key(self, predicted: str, golden: str) -> str:
        """Generate cache key for transcript pair."""
        content = f"{predicted}|{golden}|{self.provider}|{self.model}"
        return hashlib.md5(content.encode()).hexdigest()
        
    def _load_from_cache(self, cache_key: str) -> Optional[JudgeScore]:
        """Load result from cache."""
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return JudgeScore(**data)
            except Exception as e:
                logger.debug(f"Cache load error: {e}")
        return None
        
    def _save_to_cache(self, cache_key: str, score: JudgeScore):
        """Save result to cache."""
        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(score.__dict__, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Cache save error: {e}")
            
    def _call_openai(self, prompt: str) -> str:
        """Call OpenAI API."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a quality assessment expert."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise
            
    def _call_anthropic(self, prompt: str) -> str:
        """Call Anthropic API."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise
            
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response and extract scores."""
        try:
            # Try to extract JSON from response
            response = response.strip()
            
            # Find JSON block
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                data = json.loads(json_str)
                
                # Validate required fields
                required_fields = ['fidelity', 'omissions', 'insertions', 'overall', 'rationale']
                for field in required_fields:
                    if field not in data:
                        raise ValueError(f"Missing field: {field}")
                        
                return data
            else:
                raise ValueError("No JSON found in response")
                
        except Exception as e:
            logger.error(f"Response parsing error: {e}")
            logger.debug(f"Raw response: {response}")
            raise
            
    def _normalize_scores(self, scores: Dict[str, Any]) -> Dict[str, float]:
        """Normalize scores to 0-1 scale."""
        normalized = {}
        
        # Fidelity and overall: higher is better (0-5 -> 0-1)
        normalized['fidelity_norm'] = scores['fidelity'] / 5.0
        normalized['overall_norm'] = scores['overall'] / 5.0
        
        # Omissions and insertions: lower is better (5-0 -> 0-1)
        normalized['omissions_norm'] = 1.0 - (scores['omissions'] / 5.0)
        normalized['insertions_norm'] = 1.0 - (scores['insertions'] / 5.0)
        
        # Composite score: weighted average
        weights = {
            'fidelity': 0.4,
            'omissions': 0.2,
            'insertions': 0.2,
            'overall': 0.2
        }
        
        composite = (
            weights['fidelity'] * normalized['fidelity_norm'] +
            weights['omissions'] * normalized['omissions_norm'] +
            weights['insertions'] * normalized['insertions_norm'] +
            weights['overall'] * normalized['overall_norm']
        )
        
        normalized['composite_score'] = composite
        
        return normalized
        
    def evaluate_transcript(self, 
                           predicted: str,
                           golden: str,
                           conn_id: str,
                           sample_id: str) -> JudgeScore:
        """Evaluate a single transcript against golden reference."""
        
        # Check cache first
        cache_key = self._get_cache_key(predicted, golden)
        cached_result = self._load_from_cache(cache_key)
        if cached_result:
            logger.debug(f"Using cached result for {sample_id}")
            cached_result.conn_id = conn_id
            cached_result.sample_id = sample_id
            return cached_result
            
        if not self.client:
            logger.error("LLM client not initialized")
            return JudgeScore(conn_id=conn_id, sample_id=sample_id)
            
        # Prepare prompt
        prompt = f"""{self.judge_prompt}

**GOLDEN REFERENCE:**
{golden}

**PREDICTED TRANSCRIPT:**
{predicted}

Evaluate the predicted transcript against the golden reference."""
        
        try:
            # Call LLM API
            if self.provider == "openai":
                response = self._call_openai(prompt)
            elif self.provider == "anthropic":
                response = self._call_anthropic(prompt)
            else:
                raise ValueError(f"Unsupported provider: {self.provider}")
                
            # Parse response
            scores = self._parse_response(response)
            
            # Normalize scores
            normalized = self._normalize_scores(scores)
            
            # Create result
            result = JudgeScore(
                conn_id=conn_id,
                sample_id=sample_id,
                fidelity=scores['fidelity'],
                omissions=scores['omissions'],
                insertions=scores['insertions'],
                overall=scores['overall'],
                fidelity_norm=normalized['fidelity_norm'],
                omissions_norm=normalized['omissions_norm'],
                insertions_norm=normalized['insertions_norm'],
                overall_norm=normalized['overall_norm'],
                composite_score=normalized['composite_score'],
                rationale=scores['rationale'],
                raw_response=response
            )
            
            # Cache result
            self._save_to_cache(cache_key, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Judge evaluation error for {sample_id}: {e}")
            return JudgeScore(conn_id=conn_id, sample_id=sample_id)
            
    def evaluate_batch(self, 
                      transcripts: Dict[str, Dict[str, str]],
                      manifest: Dict[str, Dict[str, str]],
                      max_concurrent: int = 5) -> Dict[str, JudgeScore]:
        """Evaluate multiple transcripts with rate limiting."""
        
        results = {}
        batch_count = 0
        
        for conn_id, transcript_info in transcripts.items():
            predicted = transcript_info.get('transcript', '')
            sample_id = transcript_info.get('sample_id', '')
            
            if not predicted.strip():
                logger.warning(f"Empty transcript for {sample_id}")
                continue
                
            # Find golden reference
            golden = None
            for manifest_entry in manifest.values():
                if manifest_entry.get('sample_id') == sample_id:
                    golden_path = manifest_entry.get('golden_path')
                    if golden_path:
                        try:
                            with open(golden_path, 'r', encoding='utf-8') as f:
                                golden = f.read().strip()
                            break
                        except Exception as e:
                            logger.error(f"Error loading golden {golden_path}: {e}")
                            
            if not golden:
                logger.warning(f"No golden reference found for {sample_id}")
                continue
                
            # Evaluate
            score = self.evaluate_transcript(predicted, golden, conn_id, sample_id)
            results[conn_id] = score
            
            # Rate limiting
            batch_count += 1
            if batch_count % max_concurrent == 0:
                time.sleep(1.0)  # 1 second pause every batch
                
        return results
        
    def calculate_aggregate_scores(self, results: Dict[str, JudgeScore]) -> Dict[str, float]:
        """Calculate aggregate scores across all evaluations."""
        
        if not results:
            return {}
            
        # Collect all scores
        fidelity_scores = [r.fidelity_norm for r in results.values()]
        omissions_scores = [r.omissions_norm for r in results.values()]
        insertions_scores = [r.insertions_norm for r in results.values()]
        overall_scores = [r.overall_norm for r in results.values()]
        composite_scores = [r.composite_score for r in results.values()]
        
        def mean_std(values):
            mean_val = sum(values) / len(values)
            variance = sum((x - mean_val) ** 2 for x in values) / len(values)
            std_val = variance ** 0.5
            return mean_val, std_val
            
        fidelity_mean, fidelity_std = mean_std(fidelity_scores)
        omissions_mean, omissions_std = mean_std(omissions_scores)
        insertions_mean, insertions_std = mean_std(insertions_scores)
        overall_mean, overall_std = mean_std(overall_scores)
        composite_mean, composite_std = mean_std(composite_scores)
        
        return {
            'fidelity_mean': fidelity_mean,
            'fidelity_std': fidelity_std,
            'omissions_mean': omissions_mean,
            'omissions_std': omissions_std,
            'insertions_mean': insertions_mean,
            'insertions_std': insertions_std,
            'overall_mean': overall_mean,
            'overall_std': overall_std,
            'composite_mean': composite_mean,
            'composite_std': composite_std,
            'num_evaluations': len(results)
        }
        
    def save_results(self, 
                    results: Dict[str, JudgeScore],
                    aggregate_scores: Dict[str, float],
                    output_path: Path) -> None:
        """Save judge results to JSON file."""
        
        # Convert results to serializable format
        serializable_results = {}
        for conn_id, score in results.items():
            serializable_results[conn_id] = {
                'conn_id': score.conn_id,
                'sample_id': score.sample_id,
                'fidelity': score.fidelity,
                'omissions': score.omissions,
                'insertions': score.insertions,
                'overall': score.overall,
                'fidelity_norm': score.fidelity_norm,
                'omissions_norm': score.omissions_norm,
                'insertions_norm': score.insertions_norm,
                'overall_norm': score.overall_norm,
                'composite_score': score.composite_score,
                'rationale': score.rationale,
                'raw_response': score.raw_response
            }
            
        output_data = {
            'aggregate_scores': aggregate_scores,
            'per_connection_scores': serializable_results,
            'metadata': {
                'provider': self.provider,
                'model': self.model,
                'num_evaluations': len(results),
                'cache_enabled': True
            }
        }
        
        # Save to file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
        logger.info(f"Saved judge results to {output_path}")


def compare_quality_llm(transcripts: Dict[str, Dict[str, str]],
                       manifest: Dict[str, Dict[str, str]],
                       output_path: Path,
                       provider: str = "openai",
                       model: str = "gpt-4o-mini",
                       api_key: Optional[str] = None) -> Dict[str, JudgeScore]:
    """Main function for LLM-based quality comparison."""
    
    # Create judge
    judge = LLMJudge(
        provider=provider,
        model=model,
        api_key=api_key,
        cache_dir="cache/judge"
    )
    
    # Evaluate transcripts
    results = judge.evaluate_batch(transcripts, manifest)
    
    # Calculate aggregate scores
    aggregate_scores = judge.calculate_aggregate_scores(results)
    
    # Save results
    judge.save_results(results, aggregate_scores, output_path)
    
    return results
