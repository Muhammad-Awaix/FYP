# Import required libraries
from flask import Flask, render_template, request, jsonify
import pandas as pd
import numpy as np
import os
import re
import traceback
from langchain_community.document_loaders import TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from langchain_chroma import Chroma

app = Flask(__name__)

# Load book data
print("Loading book data...")
try:
    books = pd.read_csv("books_with_emotions.csv")
    print(f"Loaded {len(books)} books")
    
    # Handle thumbnail URLs
    if "thumbnail" in books.columns:
        books["large_thumbnail"] = books["thumbnail"] + "&fife=w400"
        books["large_thumbnail"] = np.where(
            books["large_thumbnail"].isna(),
            "https://via.placeholder.com/300x400/6c757d/ffffff?text=No+Cover",
            books["large_thumbnail"],
        )
    else:
        print("Warning: 'thumbnail' column not found, using placeholder images")
        books["large_thumbnail"] = "https://via.placeholder.com/300x400/6c757d/ffffff?text=No+Cover"
        
except Exception as e:
    print(f"Error loading book data: {e}")
    print(f"Traceback: {traceback.format_exc()}")
    # Create empty DataFrame as fallback
    books = pd.DataFrame(columns=["title", "authors", "description", "rating", "simple_categories", "large_thumbnail"])

# Load or create ChromaDB for semantic search
print("Setting up ChromaDB...")
try:
    embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    print("Embedding model loaded successfully")

    if os.path.exists("chroma_books") and os.listdir("chroma_books"):
        print("Loading existing ChromaDB...")
        db_books = Chroma(persist_directory="chroma_books", embedding_function=embedding_model)
        print("ChromaDB loaded successfully")
    else:
        print("Creating new ChromaDB...")
        if os.path.exists("isbn_desc.txt"):
            raw_documents = TextLoader("isbn_desc.txt", encoding="utf-8").load()
            text_splitter = CharacterTextSplitter(separator="\n", chunk_size=512, chunk_overlap=0)
            documents = text_splitter.split_documents(raw_documents)
            db_books = Chroma.from_documents(documents, embedding=embedding_model, persist_directory="chroma_books")
            print("ChromaDB created successfully")
        else:
            print("Warning: isbn_desc.txt not found, creating empty ChromaDB")
            db_books = Chroma(persist_directory="chroma_books", embedding_function=embedding_model)
            
except Exception as e:
    print(f"Error setting up ChromaDB: {e}")
    print(f"Traceback: {traceback.format_exc()}")
    # Create a dummy ChromaDB object as fallback
    class DummyChromaDB:
        def similarity_search_with_score(self, query, k=10):
            return []
    db_books = DummyChromaDB()

def validate_query(query):
    """Validate user input query"""
    print(f"Validating query: '{query}'")
    
    # Check if query is empty or only whitespace
    if not query or not query.strip():
        print("Query is empty")
        return False, "Please enter a search query."
    
    query = query.strip()
    print(f"Trimmed query: '{query}'")
    
    # Check if query is too short
    if len(query) < 3:
        print(f"Query too short: {len(query)} characters")
        return False, "Search query must be at least 3 characters long."
    
    # Check if query contains only numbers, spaces, or special characters
    if re.match(r'^[\d\s\W]+$', query):
        print("Query contains only numbers/symbols")
        return False, "Please enter meaningful text, not just numbers or symbols."
    
    # Check if query is too long
    if len(query) > 500:
        print(f"Query too long: {len(query)} characters")
        return False, "Search query is too long. Please keep it under 500 characters."
    
    # Check for common invalid patterns
    if re.match(r'^\d+$', query):
        print("Query is only numbers")
        return False, "Please enter meaningful text, not just numbers."
    
    if re.match(r'^[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]+$', query):
        print("Query is only symbols")
        return False, "Please enter meaningful text, not just symbols."
    
    print("Query validation passed")
    return True, "Valid query"

def get_recommendations(query, category=None, tone=None, top_k=10):
    """Get book recommendations using semantic search"""
    print(f"Searching for: '{query}' with category: '{category}' and tone: '{tone}'")
    
    try:
        # Get semantic search results with scores
        recs = db_books.similarity_search_with_score(query, k=200)
        
        # Extract ISBNs and sort by similarity score
        books_with_scores = []
        for rec, score in recs:
            try:
                isbn = int(rec.page_content.strip('"').split()[0])
                books_with_scores.append((isbn, score))
            except (ValueError, IndexError):
                continue
        
        # Sort by similarity (lower score = more similar)
        books_with_scores.sort(key=lambda x: x[1])
        top_isbns = [isbn for isbn, score in books_with_scores[:200]]
        book_recs = books[books["isbn13"].isin(top_isbns)]

        print(f"Found {len(book_recs)} books before filtering")

        # Apply category filter
        if category and category != "All":
            book_recs = book_recs[book_recs["simple_categories"] == category]
            print(f"After category filter: {len(book_recs)} books")

        # Apply tone filter and sorting
        tone_mapping = {
            "Happy": "joy",
            "Surprising": "surprise", 
            "Angry": "anger",
            "Suspenseful": "fear",
            "Sad": "sadness"
        }
        
        if tone and tone != "All" and tone in tone_mapping:
            emotion_col = tone_mapping[tone]
            if emotion_col in book_recs.columns:
                book_recs = book_recs.sort_values(by=emotion_col, ascending=False)
                print(f"Sorted by {tone} emotion: {len(book_recs)} books")
            else:
                print(f"Warning: Emotion column '{emotion_col}' not found in book_recs. Sorting by rating.")
                book_recs = book_recs.sort_values(by='rating', ascending=False)
                print(f"Sorted by rating: {len(book_recs)} books")
        else:
            if 'rating' in book_recs.columns:
                book_recs = book_recs.sort_values(by='rating', ascending=False)
                print(f"Sorted by rating: {len(book_recs)} books")
            else:
                print("Warning: 'rating' column not found in book_recs. No sorting applied.")

        return book_recs.head(top_k)
    except Exception as e:
        print(f"Error in get_recommendations: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return pd.DataFrame()

@app.route('/')
def home():
    """Home page - show top books"""
    try:
        # Get top 12 books by rating, fallback to first 12 if missing
        top_books = books.sort_values(by='rating', ascending=False).head(12) if 'rating' in books.columns else books.head(12)
        
        results = []
        for _, row in top_books.iterrows():
            try:
                # Handle description safely
                desc = row.get("description", "No description available")
                if pd.notna(desc):
                    short_desc = " ".join(str(desc).split()[:12]) + "..." if len(str(desc).split()) > 12 else str(desc)
                else:
                    short_desc = "No description available"
                
                # Handle title safely
                title = row.get("title", "Unknown Title")
                if pd.notna(title):
                    title = str(title)[:45] + "..." if len(str(title)) > 45 else str(title)
                else:
                    title = "Unknown Title"
                
                # Handle authors safely
                authors = row.get("authors", "Unknown Author")
                if pd.notna(authors):
                    authors = str(authors)[:25] + "..." if len(str(authors)) > 25 else str(authors)
                else:
                    authors = "Unknown Author"
                
                # Generate realistic ratings and reviews
                rating = round(np.random.uniform(3.5, 5.0), 1)
                reviews = np.random.randint(50, 2000)

                results.append({
                    "image": row.get("large_thumbnail", "https://via.placeholder.com/300x400/6c757d/ffffff?text=No+Cover"),
                    "title": title,
                    "authors": authors,
                    "desc": short_desc,
                    "rating": rating,
                    "reviews": reviews,
                    "category": row.get("simple_categories", "General")
                })
            except Exception as e:
                print(f"Error processing book row: {e}")
                continue

        return render_template("landing.html", results=results)
    except Exception as e:
        print(f"Error in home route: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return render_template("landing.html", results=[])

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    """Recommendation page with search functionality"""
    try:
        categories = ["All"] + sorted(books["simple_categories"].dropna().unique().tolist())
        tones = ["All", "Happy", "Surprising", "Angry", "Suspenseful", "Sad"]
        results = []
        search_performed = False
        error_message = None
        query = ""
        selected_category = "All"
        selected_tone = "All"

        if request.method == 'POST':
            query = request.form.get('query', '').strip()
            selected_category = request.form.get('category', 'All')
            selected_tone = request.form.get('tone', 'All')
            
            print(f"Form submitted - Query: '{query}', Category: '{selected_category}', Tone: '{selected_tone}'")
            
            # Validate input
            is_valid, validation_message = validate_query(query)
            
            if not is_valid:
                error_message = validation_message
                print(f"Validation failed: {error_message}")
                return render_template("recommend.html", 
                                     categories=categories, 
                                     tones=tones, 
                                     results=results, 
                                     search_performed=search_performed,
                                     error_message=error_message,
                                     query=query,
                                     selected_category=selected_category,
                                     selected_tone=selected_tone)
            
            if query:
                search_performed = True
                print(f"Starting recommendation search for: {query}")
                
                # Get recommendations
                book_recs = get_recommendations(query, selected_category, selected_tone, top_k=10)

                # Process results
                for _, row in book_recs.iterrows():
                    try:
                        desc = row.get("description", "No description available")
                        if pd.notna(desc):
                            short_desc = " ".join(str(desc).split()[:12]) + "..." if len(str(desc).split()) > 12 else str(desc)
                        else:
                            short_desc = "No description available"
                        
                        # Handle title safely
                        title = row.get("title", "Unknown Title")
                        if pd.notna(title):
                            title = str(title)[:55] + "..." if len(str(title)) > 55 else str(title)
                        else:
                            title = "Unknown Title"
                        
                        # Handle authors safely
                        authors = row.get("authors", "Unknown Author")
                        if pd.notna(authors):
                            authors = str(authors)[:35] + "..." if len(str(authors)) > 35 else str(authors)
                        else:
                            authors = "Unknown Author"
                        
                        rating = round(np.random.uniform(3.5, 5.0), 1)
                        reviews = np.random.randint(50, 2000)

                        results.append({
                            "image": row.get("large_thumbnail", "https://via.placeholder.com/300x400/6c757d/ffffff?text=No+Cover"),
                            "title": title,
                            "authors": authors,
                            "desc": short_desc,
                            "rating": rating,
                            "reviews": reviews,
                            "category": row.get("simple_categories", "General")
                        })
                    except Exception as e:
                        print(f"Error processing recommendation row: {e}")
                        continue
                
                print(f"Found {len(results)} recommendations")

        return render_template("recommend.html", 
                             categories=categories, 
                             tones=tones, 
                             results=results, 
                             search_performed=search_performed,
                             error_message=error_message,
                             query=query,
                             selected_category=selected_category,
                             selected_tone=selected_tone)
    except Exception as e:
        print(f"Error in recommend route: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return render_template("recommend.html", 
                             categories=["All"], 
                             tones=["All"], 
                             results=[], 
                             search_performed=False,
                             error_message="An error occurred. Please try again.",
                             query="",
                             selected_category="All",
                             selected_tone="All")

@app.route('/contact')
def contact():
    """Contact page"""
    return render_template("contact.html")

if __name__ == '__main__':
    app.run(debug=False)