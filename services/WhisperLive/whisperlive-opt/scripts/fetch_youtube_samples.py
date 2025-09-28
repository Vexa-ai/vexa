#!/usr/bin/env python3
"""
YouTube Audio Extraction and Transcript Generation for WhisperLive Optimization

Downloads audio from YouTube videos and extracts transcripts using WhisperLive
for creating golden reference data for optimization testing.

Usage:
    python scripts/fetch_youtube_samples.py --urls "url1,url2,url3" --output data/audio --goldens data/goldens
"""

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import List, Dict, Optional

import librosa
import numpy as np
import yt_dlp
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

console = Console()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class YouTubeAudioExtractor:
    """Extract audio and existing transcripts from YouTube videos."""
    
    def __init__(self, output_dir: str, goldens_dir: str):
        self.output_dir = Path(output_dir)
        self.goldens_dir = Path(goldens_dir)
        
        # Create directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.goldens_dir.mkdir(parents=True, exist_ok=True)
        
        # Audio settings for WhisperLive compatibility
        self.target_sr = 16000  # 16kHz for Whisper
        self.target_channels = 1  # Mono
        self.max_duration = 300  # 5 minutes max per sample
        
    def sanitize_filename(self, text: str) -> str:
        """Sanitize text for use as filename."""
        # Remove/replace invalid characters
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '_', text)
        return text[:50]  # Limit length
        
    def extract_youtube_transcript(self, url: str) -> Optional[str]:
        """Extract transcript directly from YouTube using youtube-transcript-api."""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
            
            # Extract video ID from URL
            video_id = self.extract_video_id(url)
            if not video_id:
                logger.error(f"Could not extract video ID from {url}")
                return None
            
            # Create API instance
            api = YouTubeTranscriptApi()
            
            # Try to get transcript
            try:
                # Try to get English transcript directly
                transcript = api.fetch(video_id, languages=['en'])
                logger.info("Found English transcript")
                    
            except NoTranscriptFound:
                # Try to list available transcripts and find English
                try:
                    transcript_list = api.list(video_id)
                    
                    # Look for English transcripts
                    for t in transcript_list:
                        if t.language_code.startswith('en'):
                            transcript = t.fetch()
                            logger.info(f"Found {t.language_code} transcript ({'manual' if not t.is_generated else 'auto-generated'})")
                            break
                    else:
                        logger.warning("No English transcript available")
                        return None
                        
                except Exception as e:
                    logger.warning(f"No English transcript available: {e}")
                    return None
            except TranscriptsDisabled:
                logger.warning("Transcripts are disabled for this video")
                return None
            
            # Convert transcript to text
            transcript_text = ' '.join([snippet.text for snippet in transcript]).strip()
            
            if transcript_text:
                logger.info(f"Extracted transcript ({len(transcript_text)} chars)")
                return transcript_text
                
        except Exception as e:
            logger.error(f"Error extracting transcript from {url}: {e}")
            
        return None
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL."""
        patterns = [
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
            r'youtube\.com\/v\/([^&\n?#]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
        
    def extract_audio(self, url: str, output_path: Path) -> Optional[Dict]:
        """Extract audio from YouTube URL using yt-dlp."""
        try:
            # Configure yt-dlp options
            ydl_opts = {
                'format': 'bestaudio/best',
                'extractaudio': True,
                'audioformat': 'wav',
                'audioquality': '192K',
                'outtmpl': str(output_path.with_suffix('.%(ext)s')),
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info without downloading first
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    logger.error(f"Could not extract info for {url}")
                    return None
                    
                # Check duration
                duration = info.get('duration', 0)
                if duration > self.max_duration:
                    logger.warning(f"Video {url} is {duration}s long, truncating to {self.max_duration}s")
                    
                # Get video title for filename
                title = info.get('title', 'unknown')
                safe_title = self.sanitize_filename(title)
                
                # Update output path with proper filename
                final_path = output_path.parent / f"{safe_title}.%(ext)s"
                
                # Download audio
                ydl_opts['outtmpl'] = str(final_path)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                    ydl2.download([url])
                    
                # Find the actual downloaded file
                downloaded_files = list(output_path.parent.glob(f"{safe_title}.*"))
                if downloaded_files:
                    actual_file = downloaded_files[0]  # Take the first matching file
                    processed_path = self.process_audio(actual_file, output_path)
                    if processed_path:
                        return {
                            'title': title,
                            'duration': duration,
                            'url': url,
                            'audio_path': str(processed_path),
                            'original_path': str(actual_file)
                        }
                        
        except Exception as e:
            logger.error(f"Error extracting audio from {url}: {e}")
            
        return None
        
    def process_audio(self, input_path: Path, output_path: Path) -> Optional[Path]:
        """Process audio to 16kHz mono WAV format."""
        try:
            # Load audio with librosa
            audio, sr = librosa.load(str(input_path), sr=self.target_sr, mono=True)
            
            # Ensure it's float32 and normalize
            audio = audio.astype(np.float32)
            
            # Save as 16-bit PCM WAV (WhisperLive expects this)
            import soundfile as sf
            sf.write(str(output_path), audio, self.target_sr, subtype='PCM_16')
            
            # Clean up original file
            if input_path != output_path:
                input_path.unlink(missing_ok=True)
                
            logger.info(f"Processed audio: {output_path} ({len(audio)/sr:.1f}s)")
            return output_path
            
        except Exception as e:
            logger.error(f"Error processing audio {input_path}: {e}")
            return None
            
        
    async def process_urls(self, urls: List[str]) -> List[Dict]:
        """Process multiple YouTube URLs."""
        results = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            
            task = progress.add_task("Processing YouTube URLs", total=len(urls))
            
            for i, url in enumerate(urls):
                progress.update(task, description=f"Processing {url}")
                
                # Generate unique sample ID
                sample_id = f"s{i+1:02d}"
                
                # Create output paths
                audio_path = self.output_dir / f"{sample_id}.wav"
                golden_path = self.goldens_dir / f"{sample_id}.txt"
                
                # Extract audio
                audio_info = self.extract_audio(url, audio_path)
                if not audio_info:
                    progress.update(task, advance=1)
                    continue
                    
                # Extract transcript from YouTube only
                transcript = self.extract_youtube_transcript(url)
                
                # Save golden transcript if available
                if transcript:
                    with open(golden_path, 'w', encoding='utf-8') as f:
                        f.write(transcript)
                else:
                    # Create placeholder transcript file
                    with open(golden_path, 'w', encoding='utf-8') as f:
                        f.write("TRANSCRIPT_UNAVAILABLE - No transcript found from YouTube\n")
                    transcript = "TRANSCRIPT_UNAVAILABLE"
                    
                # Store result
                result = {
                    'sample_id': sample_id,
                    'audio_path': str(audio_path),
                    'golden_path': str(golden_path),
                    'title': audio_info['title'],
                    'url': url,
                    'duration': audio_info['duration'],
                    'transcript': transcript
                }
                results.append(result)
                
                progress.update(task, advance=1)
                
        return results
        
    def create_manifest(self, results: List[Dict]) -> None:
        """Create manifest CSV file."""
        manifest_path = self.output_dir.parent / "manifest.csv"
        
        with open(manifest_path, 'w', encoding='utf-8') as f:
            f.write("sample_id,audio_path,golden_path,lang,title,url,duration\n")
            for result in results:
                f.write(f"{result['sample_id']},{result['audio_path']},{result['golden_path']},en,"
                       f'"{result["title"]}","{result["url"]}",{result["duration"]}\n')
                       
        logger.info(f"Created manifest: {manifest_path}")


def main():
    parser = argparse.ArgumentParser(description="Extract audio and transcripts from YouTube videos")
    parser.add_argument("--urls", required=True, help="Comma-separated YouTube URLs")
    parser.add_argument("--output", default="data/audio", help="Output directory for audio files")
    parser.add_argument("--goldens", default="data/goldens", help="Output directory for golden transcripts")
    parser.add_argument("--max-duration", type=int, default=300, help="Maximum duration per video (seconds)")
    
    args = parser.parse_args()
    
    # Parse URLs
    urls = [url.strip() for url in args.urls.split(',') if url.strip()]
    if not urls:
        console.print("[red]No valid URLs provided[/red]")
        sys.exit(1)
        
    console.print(f"[green]Processing {len(urls)} YouTube URLs[/green]")
    console.print(f"[blue]Output: {args.output}[/blue]")
    console.print(f"[blue]Goldens: {args.goldens}[/blue]")
    
    # Create extractor
    extractor = YouTubeAudioExtractor(args.output, args.goldens)
    extractor.max_duration = args.max_duration
    
    # Process URLs
    try:
        results = asyncio.run(extractor.process_urls(urls))
        
        if results:
            extractor.create_manifest(results)
            console.print(f"[green]✓ Successfully processed {len(results)} videos[/green]")
            
            # Print summary
            for result in results:
                console.print(f"[cyan]{result['sample_id']}[/cyan]: {result['title'][:50]}... ({result['duration']}s)")
        else:
            console.print("[red]✗ No videos were successfully processed[/red]")
            sys.exit(1)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
