import os
import sys
import logging
import argparse
from pathlib import Path

# Adjust imports based on local codebase structure
# Assuming vision_parser is in the same directory (or adjust to from rag.vision_parser import ...)
try:
    from vision_parser import VisionParser
except ImportError:
    logging.error("Could not import VisionParser. Ensure vision_parser.py is in the Python path.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


def scrape_screener(ticker: str) -> None:
    """
    Placeholder for the PDF scraper.
    In a real scenario, this function would connect to Screener.in or BSE/NSE
    and download the relevant PDFs (Annual Reports, Quarterly Results, etc.)
    to data/raw/{ticker}/.
    """
    raw_dir = Path("data/raw") / ticker
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    # Simulate downloading if directory is empty
    if not any(raw_dir.iterdir()):
        logger.info(f"[{ticker}] Simulated scrape: Please place PDF files in {raw_dir}")
    else:
        logger.info(f"[{ticker}] Found existing PDF files in {raw_dir}")


def parse_pdf_with_vision(pdf_path: Path, output_md_path: Path, parser: VisionParser) -> None:
    """
    Wraps the vision_parser logic to extract tabular data while keeping standard
    text extraction for narrative pages.
    """
    # Call the vision parser's hybrid parsing method
    # It will automatically skip table pages and use the fast text extractor for narrative
    parser.parse_pdf(
        pdf_path=str(pdf_path),
        output_md_path=str(output_md_path),
        hybrid=True,
        use_cache=False,  # We handle our own caching via idempotency check below
    )


def onboard_tenant(ticker: str) -> None:
    """
    End-to-end pipeline to onboard a new company ticker.
    Stage 1: Scrape/Download PDFs.
    Stage 2: Vision-Language Model Parsing.
    """
    ticker = ticker.upper().strip()
    logger.info(f"Starting onboarding pipeline for tenant: {ticker}")

    # Stage 1: Download
    logger.info(f"[{ticker}] STAGE 1: Scraping documents...")
    try:
        scrape_screener(ticker)
    except Exception as e:
        logger.error(f"[{ticker}] Failed to scrape documents: {e}")
        return

    # Stage 2: Vision Parsing
    logger.info(f"[{ticker}] STAGE 2: VLM Parsing...")
    
    raw_dir = Path("data/raw") / ticker
    parsed_dir = Path("data/parsed_cache") / ticker
    
    # Ensure directories exist
    if not raw_dir.exists():
        logger.error(f"[{ticker}] Raw directory {raw_dir} does not exist. Aborting.")
        return
        
    parsed_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = list(raw_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"[{ticker}] No PDF files found in {raw_dir}.")
        return

    logger.info(f"[{ticker}] Found {len(pdf_files)} PDF(s) to process.")

    # Initialize the parser once to reuse the client
    try:
        parser = VisionParser()
    except Exception as e:
        logger.error(f"[{ticker}] Failed to initialize VisionParser: {e}")
        return

    success_count = 0
    skip_count = 0
    fail_count = 0

    for pdf_path in pdf_files:
        output_md_path = parsed_dir / f"{pdf_path.stem}.md"

        # Guardrail: Idempotency
        if output_md_path.exists():
            logger.info(f"[{ticker}] SKIPPING (Already parsed): {pdf_path.name}")
            skip_count += 1
            continue

        logger.info(f"[{ticker}] PARSING: {pdf_path.name}...")
        
        # Guardrail: Fault Tolerance
        try:
            parse_pdf_with_vision(pdf_path, output_md_path, parser)
            logger.info(f"[{ticker}] SUCCESS: Saved to {output_md_path.name}")
            success_count += 1
        except Exception as e:
            logger.error(f"[{ticker}] FAILED on {pdf_path.name}: {type(e).__name__} - {e}")
            fail_count += 1
            # Continue to next PDF instead of crashing the pipeline
            continue

    # Stage 3: Markdown Section Splitting & Qdrant Upsert
    logger.info(f"[{ticker}] STAGE 3: Chunking & Qdrant Upsert...")
    parsed_files = list(parsed_dir.glob("*.md"))
    
    if not parsed_files:
        logger.warning(f"[{ticker}] No parsed markdown files found for Stage 3.")
        return

    import re
    try:
        import voyageai
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct, VectorParams, Distance
    except ImportError as e:
        logger.error(f"[{ticker}] Stage 3 failed. Missing dependencies: {e}")
        logger.error("Run: pip install voyageai qdrant-client")
        return

    # Check API keys
    voyage_api_key = os.getenv("VOYAGE_API_KEY")
    if not voyage_api_key or voyage_api_key == "replace_me":
        logger.error(f"[{ticker}] VOYAGE_API_KEY is not set in .env")
        return

    # Initialize Clients
    vo = voyageai.Client(api_key=voyage_api_key)
    # Using local Qdrant memory/file storage for now. In production, connect to a Qdrant server.
    qdrant = QdrantClient(path="qdrant_db")
    
    collection_name = "financial_docs"
    
    # Ensure collection exists
    try:
        qdrant.get_collection(collection_name)
    except Exception:
        logger.info(f"[{ticker}] Creating Qdrant collection: {collection_name}")
        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )

    # Regex to split on "### [PAGE_N]"
    page_pattern = re.compile(r"### \[PAGE_(\d+)\]")
    
    chunks = []
    
    for md_file in parsed_files:
        content = md_file.read_text(encoding="utf-8")
        # Split by page headers
        parts = page_pattern.split(content)
        
        # parts[0] is usually empty or preamble before the first page
        # parts[1], parts[3], parts[5]... are page numbers
        # parts[2], parts[4], parts[6]... are the page text
        for i in range(1, len(parts), 2):
            page_num_str = parts[i]
            page_text = parts[i+1].strip()
            
            if not page_text:
                continue
                
            page_num = int(page_num_str)
            chunks.append({
                "ticker": ticker,
                "source": md_file.name,
                "page": page_num,
                "text": page_text
            })

    if not chunks:
        logger.info(f"[{ticker}] No text chunks extracted from markdown files.")
        return
        
    logger.info(f"[{ticker}] Extracted {len(chunks)} page-level chunks. Getting embeddings...")

    # Batch send to Voyage-finance-2 (max 72 chunks per batch usually, but let's batch by 50)
    batch_size = 50
    points = []
    
    # Need unique IDs for Qdrant (can use uuid or simple hash)
    import hashlib
    
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        texts = [c["text"] for c in batch]
        
        try:
            logger.info(f"[{ticker}] Embedding batch {i//batch_size + 1}/{(len(chunks)-1)//batch_size + 1}...")
            # Embed with Voyage
            result = vo.embed(texts, model="voyage-finance-2", input_type="document")
            embeddings = result.embeddings
            
            # Create Qdrant points
            for j, chunk in enumerate(batch):
                chunk_id_str = f"{chunk['ticker']}_{chunk['source']}_page_{chunk['page']}"
                point_id = hashlib.md5(chunk_id_str.encode()).hexdigest()
                
                points.append(PointStruct(
                    id=point_id,
                    vector=embeddings[j],
                    payload={
                        "ticker": chunk["ticker"],
                        "source": chunk["source"],
                        "page": chunk["page"],
                        "text": chunk["text"]
                    }
                ))
        except Exception as e:
            logger.error(f"[{ticker}] Voyage Embedding failed on batch {i//batch_size + 1}: {e}")
            continue

    if points:
        logger.info(f"[{ticker}] Upserting {len(points)} vectors to Qdrant...")
        try:
            qdrant.upsert(
                collection_name=collection_name,
                points=points
            )
            logger.info(f"[{ticker}] Qdrant Upsert complete!")
        except Exception as e:
            logger.error(f"[{ticker}] Qdrant Upsert failed: {e}")

    # Summary
    logger.info(f"[{ticker}] Onboarding pipeline completed.")
    logger.info(f"[{ticker}] Summary: {success_count} parsed, {skip_count} skipped, {fail_count} failed.")
    logger.info(f"[{ticker}] Embedded and stored {len(points)} page chunks in Qdrant.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Onboard a new company by scraping and parsing financial PDFs.")
    parser.add_argument("ticker", type=str, help="The stock ticker symbol (e.g., SUNPHARMA, HINDUNILVR)")
    
    args = parser.parse_args()
    
    onboard_tenant(args.ticker)
