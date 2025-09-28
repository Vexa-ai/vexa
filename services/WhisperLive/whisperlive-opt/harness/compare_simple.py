"""
Simple Text Quality Metrics for WhisperLive Optimization

Implements lightweight string-based metrics for comparing transcripts
against golden references without requiring external dependencies.
"""

import logging
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json

logger = logging.getLogger(__name__)


@dataclass
class QualityMetrics:
    """Quality metrics for a single transcript comparison."""
    conn_id: str
    sample_id: str
    golden_path: str
    
    # Character-level metrics
    char_error_rate: float = 0.0
    char_accuracy: float = 0.0
    
    # Word-level metrics
    word_error_rate: float = 0.0
    word_accuracy: float = 0.0
    
    # Token-level metrics
    token_f1: float = 0.0
    token_precision: float = 0.0
    token_recall: float = 0.0
    
    # Length metrics
    length_ratio: float = 0.0
    length_diff: int = 0
    
    # Simple overlap metrics
    jaccard_similarity: float = 0.0
    longest_common_subsequence: float = 0.0
    
    # Normalized texts
    normalized_pred: str = ""
    normalized_golden: str = ""


class TextNormalizer:
    """Normalize text for fair comparison."""
    
    def __init__(self, 
                 lowercase: bool = True,
                 remove_punctuation: bool = True,
                 remove_extra_whitespace: bool = True,
                 remove_numbers: bool = False):
        
        self.lowercase = lowercase
        self.remove_punctuation = remove_punctuation
        self.remove_extra_whitespace = remove_extra_whitespace
        self.remove_numbers = remove_numbers
        
    def normalize(self, text: str) -> str:
        """Apply normalization to text."""
        if not text:
            return ""
            
        # Convert to lowercase
        if self.lowercase:
            text = text.lower()
            
        # Remove punctuation
        if self.remove_punctuation:
            # Keep apostrophes for contractions but remove other punctuation
            text = re.sub(r"[^\w\s']", " ", text)
            # Remove apostrophes that are not part of contractions
            text = re.sub(r"(?<!\w)'(?!\w)", " ", text)
            
        # Remove numbers
        if self.remove_numbers:
            text = re.sub(r"\d+", " ", text)
            
        # Normalize whitespace
        if self.remove_extra_whitespace:
            text = re.sub(r"\s+", " ", text).strip()
            
        return text
        
    def tokenize(self, text: str) -> List[str]:
        """Tokenize normalized text."""
        return [token for token in text.split() if token]


class SimpleQualityCalculator:
    """Calculate simple quality metrics between predicted and golden text."""
    
    def __init__(self, normalizer: Optional[TextNormalizer] = None):
        self.normalizer = normalizer or TextNormalizer()
        
    def calculate_levenshtein_distance(self, s1: str, s2: str) -> int:
        """Calculate Levenshtein distance between two strings."""
        if len(s1) < len(s2):
            return self.calculate_levenshtein_distance(s2, s1)
            
        if len(s2) == 0:
            return len(s1)
            
        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
            
        return previous_row[-1]
        
    def calculate_lcs_length(self, s1: str, s2: str) -> int:
        """Calculate length of longest common subsequence."""
        m, n = len(s1), len(s2)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if s1[i-1] == s2[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])
                    
        return dp[m][n]
        
    def calculate_jaccard_similarity(self, tokens1: List[str], tokens2: List[str]) -> float:
        """Calculate Jaccard similarity between token sets."""
        set1 = set(tokens1)
        set2 = set(tokens2)
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
        
    def calculate_token_f1(self, pred_tokens: List[str], golden_tokens: List[str]) -> Tuple[float, float, float]:
        """Calculate token-level F1, precision, and recall."""
        pred_set = set(pred_tokens)
        golden_set = set(golden_tokens)
        
        true_positives = len(pred_set & golden_set)
        false_positives = len(pred_set - golden_set)
        false_negatives = len(golden_set - pred_set)
        
        precision = true_positives / len(pred_set) if len(pred_set) > 0 else 0.0
        recall = true_positives / len(golden_set) if len(golden_set) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        
        return f1, precision, recall
        
    def calculate_metrics(self, 
                         predicted: str, 
                         golden: str,
                         conn_id: str,
                         sample_id: str,
                         golden_path: str) -> QualityMetrics:
        """Calculate all quality metrics."""
        
        # Normalize texts
        norm_pred = self.normalizer.normalize(predicted)
        norm_golden = self.normalizer.normalize(golden)
        
        # Tokenize
        pred_tokens = self.normalizer.tokenize(norm_pred)
        golden_tokens = self.normalizer.tokenize(norm_golden)
        
        # Character-level metrics
        char_levenshtein = self.calculate_levenshtein_distance(norm_pred, norm_golden)
        char_error_rate = char_levenshtein / max(len(norm_golden), 1)
        char_accuracy = 1.0 - char_error_rate
        
        # Word-level metrics
        word_levenshtein = self.calculate_levenshtein_distance(" ".join(pred_tokens), " ".join(golden_tokens))
        word_error_rate = word_levenshtein / max(len(golden_tokens), 1)
        word_accuracy = 1.0 - word_error_rate
        
        # Token-level metrics
        token_f1, token_precision, token_recall = self.calculate_token_f1(pred_tokens, golden_tokens)
        
        # Length metrics
        length_ratio = len(pred_tokens) / max(len(golden_tokens), 1)
        length_diff = len(pred_tokens) - len(golden_tokens)
        
        # Overlap metrics
        jaccard_sim = self.calculate_jaccard_similarity(pred_tokens, golden_tokens)
        lcs_length = self.calculate_lcs_length(" ".join(pred_tokens), " ".join(golden_tokens))
        lcs_ratio = lcs_length / max(len(golden_tokens), 1)
        
        return QualityMetrics(
            conn_id=conn_id,
            sample_id=sample_id,
            golden_path=golden_path,
            char_error_rate=char_error_rate,
            char_accuracy=char_accuracy,
            word_error_rate=word_error_rate,
            word_accuracy=word_accuracy,
            token_f1=token_f1,
            token_precision=token_precision,
            token_recall=token_recall,
            length_ratio=length_ratio,
            length_diff=length_diff,
            jaccard_similarity=jaccard_sim,
            longest_common_subsequence=lcs_ratio,
            normalized_pred=norm_pred,
            normalized_golden=norm_golden
        )


class QualityComparator:
    """Compare transcripts against golden references."""
    
    def __init__(self, normalizer: Optional[TextNormalizer] = None):
        self.calculator = SimpleQualityCalculator(normalizer)
        
    def load_golden_transcript(self, golden_path: str) -> str:
        """Load golden transcript from file."""
        try:
            with open(golden_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"Error loading golden transcript {golden_path}: {e}")
            return ""
            
    def compare_transcript(self, 
                          predicted: str,
                          golden_path: str,
                          conn_id: str,
                          sample_id: str) -> QualityMetrics:
        """Compare a single transcript against golden reference."""
        
        # Load golden transcript
        golden = self.load_golden_transcript(golden_path)
        
        if not golden:
            logger.warning(f"Empty golden transcript for {sample_id}")
            
        # Calculate metrics
        metrics = self.calculator.calculate_metrics(
            predicted=predicted,
            golden=golden,
            conn_id=conn_id,
            sample_id=sample_id,
            golden_path=golden_path
        )
        
        return metrics
        
    def compare_batch(self, 
                     transcripts: Dict[str, Dict[str, str]],
                     manifest: Dict[str, Dict[str, str]]) -> Dict[str, QualityMetrics]:
        """Compare multiple transcripts against their golden references."""
        
        results = {}
        
        for conn_id, transcript_info in transcripts.items():
            predicted = transcript_info.get('transcript', '')
            sample_id = transcript_info.get('sample_id', '')
            
            # Find golden path for this sample
            golden_path = None
            for manifest_entry in manifest.values():
                if manifest_entry.get('sample_id') == sample_id:
                    golden_path = manifest_entry.get('golden_path')
                    break
                    
            if not golden_path:
                logger.warning(f"No golden path found for sample {sample_id}")
                continue
                
            # Compare
            metrics = self.compare_transcript(predicted, golden_path, conn_id, sample_id)
            results[conn_id] = metrics
            
        return results
        
    def calculate_aggregate_metrics(self, results: Dict[str, QualityMetrics]) -> Dict[str, float]:
        """Calculate aggregate metrics across all comparisons."""
        
        if not results:
            return {}
            
        # Collect all metric values
        char_error_rates = [m.char_error_rate for m in results.values()]
        char_accuracies = [m.char_accuracy for m in results.values()]
        word_error_rates = [m.word_error_rate for m in results.values()]
        word_accuracies = [m.word_accuracy for m in results.values()]
        token_f1s = [m.token_f1 for m in results.values()]
        token_precisions = [m.token_precision for m in results.values()]
        token_recalls = [m.token_recall for m in results.values()]
        jaccard_similarities = [m.jaccard_similarity for m in results.values()]
        lcs_ratios = [m.longest_common_subsequence for m in results.values()]
        
        return {
            'char_error_rate_mean': sum(char_error_rates) / len(char_error_rates),
            'char_error_rate_std': (sum((x - sum(char_error_rates)/len(char_error_rates))**2 for x in char_error_rates) / len(char_error_rates))**0.5,
            'char_accuracy_mean': sum(char_accuracies) / len(char_accuracies),
            'char_accuracy_std': (sum((x - sum(char_accuracies)/len(char_accuracies))**2 for x in char_accuracies) / len(char_accuracies))**0.5,
            'word_error_rate_mean': sum(word_error_rates) / len(word_error_rates),
            'word_error_rate_std': (sum((x - sum(word_error_rates)/len(word_error_rates))**2 for x in word_error_rates) / len(word_error_rates))**0.5,
            'word_accuracy_mean': sum(word_accuracies) / len(word_accuracies),
            'word_accuracy_std': (sum((x - sum(word_accuracies)/len(word_accuracies))**2 for x in word_accuracies) / len(word_accuracies))**0.5,
            'token_f1_mean': sum(token_f1s) / len(token_f1s),
            'token_f1_std': (sum((x - sum(token_f1s)/len(token_f1s))**2 for x in token_f1s) / len(token_f1s))**0.5,
            'token_precision_mean': sum(token_precisions) / len(token_precisions),
            'token_precision_std': (sum((x - sum(token_precisions)/len(token_precisions))**2 for x in token_precisions) / len(token_precisions))**0.5,
            'token_recall_mean': sum(token_recalls) / len(token_recalls),
            'token_recall_std': (sum((x - sum(token_recalls)/len(token_recalls))**2 for x in token_recalls) / len(token_recalls))**0.5,
            'jaccard_similarity_mean': sum(jaccard_similarities) / len(jaccard_similarities),
            'jaccard_similarity_std': (sum((x - sum(jaccard_similarities)/len(jaccard_similarities))**2 for x in jaccard_similarities) / len(jaccard_similarities))**0.5,
            'lcs_ratio_mean': sum(lcs_ratios) / len(lcs_ratios),
            'lcs_ratio_std': (sum((x - sum(lcs_ratios)/len(lcs_ratios))**2 for x in lcs_ratios) / len(lcs_ratios))**0.5,
            'num_samples': len(results)
        }
        
    def save_results(self, 
                    results: Dict[str, QualityMetrics],
                    aggregate_metrics: Dict[str, float],
                    output_path: Path) -> None:
        """Save quality comparison results to JSON file."""
        
        # Convert results to serializable format
        serializable_results = {}
        for conn_id, metrics in results.items():
            serializable_results[conn_id] = {
                'conn_id': metrics.conn_id,
                'sample_id': metrics.sample_id,
                'golden_path': metrics.golden_path,
                'char_error_rate': metrics.char_error_rate,
                'char_accuracy': metrics.char_accuracy,
                'word_error_rate': metrics.word_error_rate,
                'word_accuracy': metrics.word_accuracy,
                'token_f1': metrics.token_f1,
                'token_precision': metrics.token_precision,
                'token_recall': metrics.token_recall,
                'length_ratio': metrics.length_ratio,
                'length_diff': metrics.length_diff,
                'jaccard_similarity': metrics.jaccard_similarity,
                'longest_common_subsequence': metrics.longest_common_subsequence,
                'normalized_pred': metrics.normalized_pred,
                'normalized_golden': metrics.normalized_golden
            }
            
        output_data = {
            'aggregate_metrics': aggregate_metrics,
            'per_connection_results': serializable_results,
            'metadata': {
                'normalization': {
                    'lowercase': True,
                    'remove_punctuation': True,
                    'remove_extra_whitespace': True,
                    'remove_numbers': False
                },
                'num_connections': len(results)
            }
        }
        
        # Save to file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
        logger.info(f"Saved quality results to {output_path}")


def compare_quality_simple(transcripts: Dict[str, Dict[str, str]],
                          manifest: Dict[str, Dict[str, str]],
                          output_path: Path) -> Dict[str, QualityMetrics]:
    """Main function for simple quality comparison."""
    
    # Create comparator
    normalizer = TextNormalizer(
        lowercase=True,
        remove_punctuation=True,
        remove_extra_whitespace=True,
        remove_numbers=False
    )
    
    comparator = QualityComparator(normalizer)
    
    # Compare transcripts
    results = comparator.compare_batch(transcripts, manifest)
    
    # Calculate aggregate metrics
    aggregate_metrics = comparator.calculate_aggregate_metrics(results)
    
    # Save results
    comparator.save_results(results, aggregate_metrics, output_path)
    
    return results
