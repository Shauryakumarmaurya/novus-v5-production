while IFS='=' read -r key value; do
    [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
    export "$key"="$value"
done < .env
echo $GEMINI_API_KEY
python3 -c "import os; print(os.environ.get('GEMINI_API_KEY'))"
